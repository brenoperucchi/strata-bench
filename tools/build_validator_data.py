#!/usr/bin/env python3
"""Writes docs/schema.json: the public schema and the export-gate constants as DATA, so the browser and the upload worker run the SAME rules as strata_bench.validate.
The JavaScript validator (docs/validator.js) is a thin interpreter of this file; tests/test_js_validator.py checks that it returns exactly the problems the Python validator returns.

  python3 tools/build_validator_data.py           write docs/schema.json
  python3 tools/build_validator_data.py --check   exit 1 if docs/schema.json is stale
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from strata_bench import core, schema, validate  # noqa: E402


def spec(s):
    if s == schema.SCALAR:
        return "scalar"
    if s == schema.HEX:
        return "hex"
    if isinstance(s, list):
        return {"t": "list", "s": spec(s[0])}
    if isinstance(s, schema.Map):
        return {"t": "map", "re": s.key.pattern, "s": spec(s.spec)}
    if isinstance(s, dict):
        return {"t": "obj", "k": {k: spec(v) for k, v in s.items()}}
    raise TypeError(f"unknown spec {s!r}")


def data() -> dict:
    return {
        "schema_ids": schema.SCHEMA_IDS,
        "host_relations": list(schema.HOST_RELATIONS),
        "suites": list(schema.SUITES),
        "blocks": list(schema.BLOCKS),
        "public": spec(schema.PUBLIC),
        "hex_re": schema._HEX.pattern,
        "prompts": schema.STANDARD_V1_PROMPTS,
        "prompts_v2": schema.STANDARD_V2_PROMPTS,
        "skip_reasons": list(schema.SKIP_REASONS),
        "agent_protocol": schema.AGENT_PROTOCOL,
        "probe_system_sha256": core.sha256(core.PROBE_SYSTEM.encode()),
        "gprobe_system_sha256": core.sha256(core.GPROBE_SYSTEM.encode()),
        "gprobe_chars": {str(k): v for k, v in core.GPROBE_CHARS.items()},
        "failure_kinds": list(schema.FAILURE_KINDS),
        "failure_where_re": schema.FAILURE_WHERE.pattern,
        "id_re": schema.ID_RE.pattern,
        "leaks": [[rx.pattern, what] for rx, what in validate.LEAKS],
        "statuses": ["ok", "warnings", "failed"],
    }


def dump() -> str:
    return json.dumps(data(), indent=1, sort_keys=True) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "schema.json")
    a = ap.parse_args(argv)
    text = dump()
    if a.check:
        ok = a.out.exists() and a.out.read_text() == text
        print(f"{a.out} is {'up to date' if ok else 'STALE: run tools/build_validator_data.py'}")
        return 0 if ok else 1
    a.out.write_text(text)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
