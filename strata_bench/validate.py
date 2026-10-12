"""Public-export gate. A result.json may be published only if: it is a complete strata-bench/v1 (protocol standard-v1) or
strata-bench/v2 (protocol standard-v2) result of the public protocol with its canonical prompt hashes, every key at every depth is in the public schema (schema.PUBLIC), and no
value looks like a local path, a private network address or a secret. It does not prove that a free-text value is
harmless: keep free text out."""
import json
import re

from . import schema

LEAKS = [
    (re.compile(r"\b[A-Za-z]:[\\/]"), "a Windows path"),
    (re.compile(r"(?<![<\w.:/])/[A-Za-z0-9_.-]+/"), "an absolute path"),
    (re.compile(r"\b(10|127)(\.\d{1,3}){3}\b|\b192\.168(\.\d{1,3}){2}\b|\b172\.(1[6-9]|2\d|3[01])(\.\d{1,3}){2}\b"),
     "a private network address"),
    (re.compile(r"\\\\[\w.-]+\\"), "a network share"),
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{8,}|hf_[A-Za-z0-9]{8,}|gh[pousr]_[A-Za-z0-9]{8,})"), "a token"),
]


def same_flat(a, b) -> bool:
    """Two flat objects with equal values; a boolean never equals a number (Python's True == 1 would, JavaScript's does not)."""
    return (isinstance(a, dict) and set(a) == set(b)
            and all(a[k] == b[k] and isinstance(a[k], bool) == isinstance(b[k], bool) for k in b))


def version_problems(doc: dict, proto: dict) -> list:
    """v2 adds first-token timing, the suite and the host relation; v1 results must not carry them, and a v2 result must
    declare the closed ones."""
    out = []
    v2 = doc.get("schema") == "strata-bench/v2"
    runner = doc.get("runner") if isinstance(doc.get("runner"), dict) else {}
    sizes = doc.get("sizes") if isinstance(doc.get("sizes"), dict) else {}
    runs = [r for r in doc.get("runs") or [] if isinstance(r, dict)] if isinstance(doc.get("runs"), list) else []
    if not v2:
        present = [("runner.host_relation", "host_relation" in runner), ("protocol.suite", "suite" in proto),
                   ("protocol.blocks", "blocks" in proto), ("protocol.skipped", "skipped" in proto)]
        for block, data in (("agent", "agent" in doc), ("chat", "chat" in sizes), ("clients", "clients" in doc)):
            present += [(f"protocol.{block}", block in proto), ({"chat": "sizes.chat"}.get(block, block), data)]
        present += [(f"runs[].{k}", any(k in r for r in runs)) for k in schema.V2_RUN_KEYS]
        present.append(("sizes.*.ttft_ms", any(isinstance(s, dict) and "ttft_ms" in s for s in sizes.values())))
        out.extend(f"{path} is only allowed in strata-bench/v2" for path, here in present if here)
        return out
    if runner.get("host_relation") not in schema.HOST_RELATIONS:
        out.append("runner.host_relation must be one of " + ", ".join(schema.HOST_RELATIONS))
    if proto.get("suite") not in schema.SUITES:
        out.append("protocol.suite must be one of " + ", ".join(schema.SUITES))
    blocks = proto.get("blocks")
    if not (isinstance(blocks, list) and blocks and all(b in schema.BLOCKS for b in blocks)):
        out.append("protocol.blocks must be a list of " + ", ".join(schema.BLOCKS))
        return out
    skipped = proto.get("skipped")
    if skipped is not None and not (isinstance(skipped, list) and all(
            isinstance(k, dict) and k.get("block") in blocks and k.get("reason") in schema.SKIP_REASONS for k in skipped)):
        out.append("protocol.skipped must list blocks of this result with a reason from " + ", ".join(schema.SKIP_REASONS))
        skipped = []
    skipped_blocks = [k.get("block") for k in skipped or []]
    validity = doc.get("validity") if isinstance(doc.get("validity"), dict) else {}
    agent, clients = doc.get("agent"), doc.get("clients")
    # per block: (the data a finished result must carry, where that data is, what it is called in the message)
    parts = {"agent": (isinstance(agent, dict) and isinstance(agent.get("sessions"), list) and len(agent["sessions"]) > 0, "agent" in doc, "agent.sessions"),
             "chat": (isinstance(sizes.get("chat"), dict), "chat" in sizes, "sizes.chat"),
             "clients": (isinstance(clients, list) and len(clients) > 0, "clients" in doc, "clients")}
    for block, (complete, has_data, label) in parts.items():
        ran = block in blocks and block not in skipped_blocks
        if not ran and (has_data or block in proto or any(r.get("size") == block for r in runs)):
            out.append(f"{block} data is only allowed when the {block} block ran")
        elif ran and validity.get("status") != "failed" and not complete:
            out.append(f"a result with the {block} block needs {label}")
        if ran and not same_flat(proto.get(block), schema.BLOCK_PROTOCOLS[block]):
            out.append(f"protocol.{block} is not standard-v2's")
    return out


def problems(doc: dict) -> list:
    out = schema.check(doc, schema.PUBLIC)
    if not isinstance(doc, dict):
        return out
    if doc.get("schema") not in schema.SCHEMA_IDS.values():
        out.append("schema is not strata-bench/v1 or strata-bench/v2")
    proto = doc.get("protocol") or {}
    pid = proto.get("id")
    if not (isinstance(pid, str) and pid in schema.SCHEMA_IDS):
        out.append(f"protocol {pid!r} is not public")
    elif doc.get("schema") in schema.SCHEMA_IDS.values() and schema.SCHEMA_IDS[pid] != doc["schema"]:
        out.append(f"protocol {pid} is written in {schema.SCHEMA_IDS[pid]}, not {doc['schema']}")
    out.extend(version_problems(doc, proto))
    known = {**schema.STANDARD_V1_PROMPTS, **(schema.STANDARD_V2_PROMPTS if pid == "standard-v2" else {})}
    for name, h in (proto.get("prompt_sha256") or {}).items():
        if known.get(name) != h:
            out.append(f"prompt {name} is not {pid}'s")
    from .core import PROBE_SYSTEM, sha256          # the canonical probe system prompt
    if proto.get("probe_system_sha256") not in (None, sha256(PROBE_SYSTEM.encode())):
        out.append("probe system prompt is not standard-v1's")
    from .core import GPROBE_CHARS, GPROBE_SYSTEM   # the canonical gprobe, when the result has one
    gp = proto.get("gprobe") or {}
    if gp and (gp.get("system_sha256") != sha256(GPROBE_SYSTEM.encode())
               or gp.get("chars") != {str(k): v for k, v in GPROBE_CHARS.items()}):
        out.append("gprobe is not standard-v1's")
    validity = doc.get("validity") or {}
    status, failure = validity.get("status"), validity.get("failure")
    if status not in ("ok", "warnings", "failed"):
        out.append(f"result is not complete (status {status!r})")
    if status == "failed":                       # the health view: a catastrophic failure with a closed kind and a closed place
        if not isinstance(failure, dict) or failure.get("kind") not in schema.FAILURE_KINDS:
            out.append("a failed result needs failure.kind from " + ", ".join(schema.FAILURE_KINDS))
        elif not (isinstance(failure.get("where"), str) and schema.FAILURE_WHERE.match(failure["where"])):
            out.append("failure.where is not one of the protocol's call names")
    elif failure is not None:
        out.append("failure is only allowed on a failed result")
    for block, key in (("submission", "by"), ("machine", "machine_id")):      # declared names: short, no free text
        section = doc.get(block)
        if isinstance(section, dict) and section.get(key) is not None and not (
                isinstance(section[key], str) and schema.ID_RE.fullmatch(section[key])):
            out.append(f"{block}.{key} must be a short name (letters, digits, '.', '_' and '-')")
    text = json.dumps(doc)
    for rx, what in LEAKS:
        m = rx.search(text)
        if m:
            out.append(f"contains {what}: {m.group(0)!r}")
    return out
