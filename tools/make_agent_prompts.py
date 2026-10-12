#!/usr/bin/env python3
"""Writes the standard-v2 `agent` prompt files into strata_bench/prompts/ and prints their SHA-256 sums.

The five documents are fixed slices of the public xlong.txt (a source file of the Strata engine), cut at line boundaries; the system text, the tool
definitions and the five questions are written for this project. The files are committed and pinned by hash in strata_bench/schema.py: this script
only records how they were made, and `--check` verifies that the committed files are exactly what it produces.

  python3 tools/make_agent_prompts.py            write the files and print the sums
  python3 tools/make_agent_prompts.py --check    exit 1 if a committed file differs
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

PROMPTS = Path(__file__).resolve().parent.parent / "strata_bench" / "prompts"
DOC_CHARS = 7000                                   # about 2,000 tokens of C++ per document
DOC_STARTS = (3000, 22000, 41000, 60000, 79000)    # character offsets into xlong.txt, moved forward to the next line start

SYSTEM = (
    "You are a coding assistant that runs in a terminal next to a developer. The developer works on a C++ inference engine and shares its source "
    "files with you one at a time, each followed by a question. You have tools to read files, search the repository, edit files and run commands, "
    "but in this session you must not call any of them: answer in plain text, in at most four sentences, using only the files the developer has "
    "pasted so far. If a question cannot be answered from those files, say what is missing. Do not repeat the file back."
)

TOOLS = [
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a text file from the repository and return its contents, optionally only a range of lines.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Path of the file, relative to the repository root."},
            "start_line": {"type": "integer", "description": "First line to return, counting from 1."},
            "end_line": {"type": "integer", "description": "Last line to return."}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "search", "description": "Search the repository for a regular expression and return the matching lines with their file and line number.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string", "description": "Regular expression to look for."},
            "path": {"type": "string", "description": "Directory or file to search in. Defaults to the whole repository."}}, "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "edit_file", "description": "Replace one exact piece of text in a file with another. Fails if the old text is missing or appears more than once.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Path of the file to edit."},
            "old_text": {"type": "string", "description": "The exact text to replace."},
            "new_text": {"type": "string", "description": "The text that takes its place."}}, "required": ["path", "old_text", "new_text"]}}},
    {"type": "function", "function": {
        "name": "run_command", "description": "Run a shell command in the repository root and return its output and exit status.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "The command line to run."},
            "timeout_seconds": {"type": "integer", "description": "Stop the command after this many seconds."}}, "required": ["command"]}}},
]

TURNS = {
    "template": "File {n} of 5:\n\n{doc}\n\n{question}",
    "questions": [
        "In two sentences, what is this file responsible for?",
        "Which part of this file deals with memory or buffers, and how does its role differ from the first file's?",
        "What could go wrong in this file if an input is larger than expected? Name the place that would break first.",
        "Which of the three files so far would you read first to understand how work is scheduled, and why?",
        "Looking at all five files, which one is most likely to be a performance bottleneck, and what is your evidence?",
    ],
}


def files() -> dict:
    code = (PROMPTS / "xlong.txt").read_text(encoding="utf-8").split("\n", 1)[1]          # drop the v1 instruction line
    out = {"agent_system.txt": SYSTEM, "agent_tools.json": json.dumps(TOOLS, indent=1, ensure_ascii=False) + "\n",
           "agent_turns.json": json.dumps(TURNS, indent=1, ensure_ascii=False) + "\n"}
    for i, start in enumerate(DOC_STARTS, 1):
        begin = code.index("\n", start) + 1
        lines, size = [], 0
        for line in code[begin:].split("\n"):
            if size + len(line) + 1 > DOC_CHARS:
                break
            lines.append(line)
            size += len(line) + 1
        out[f"agent_doc_{i}.txt"] = "\n".join(lines) + "\n"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    bad = 0
    for name, text in files().items():
        data = text.encode("utf-8")
        path = PROMPTS / name
        if a.check:
            ok = path.exists() and path.read_bytes() == data
            bad += not ok
            print(f"{name}: {'ok' if ok else 'DIFFERS'}")
        else:
            path.write_bytes(data)
            print(f"{hashlib.sha256(data).hexdigest()}  {name}  ({len(data)} bytes)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
