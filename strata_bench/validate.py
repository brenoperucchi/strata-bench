"""Public-export gate. A result.json may be published only if: it is a complete strata-bench/v1 (protocol standard-v1) or
strata-bench/v2 (protocol standard-v2) result of the public protocol with its canonical prompt hashes, every key at every depth is in the public schema (schema.PUBLIC), and no
value looks like a local path, a private network address or a secret. It does not prove that a free-text value is
harmless: keep free text out."""
import json
import re

from . import schema

LEAKS = [
    (re.compile(r"\b[A-Za-z]:[\\/]", re.ASCII), "a Windows path"),
    (re.compile(r"(?<![<\w.:/])/[A-Za-z0-9_.-]+/", re.ASCII), "an absolute path"),
    (re.compile(r"\b(10|127)(\.\d{1,3}){3}\b|\b192\.168(\.\d{1,3}){2}\b|\b172\.(1[6-9]|2\d|3[01])(\.\d{1,3}){2}\b", re.ASCII),
     "a private network address"),
    (re.compile(r"\\\\[\w.-]+\\", re.ASCII), "a network share"),
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{8,}|hf_[A-Za-z0-9]{8,}|gh[pousr]_[A-Za-z0-9]{8,})", re.ASCII), "a token"),
]


def view(x) -> dict:
    """x when it is an object, else an empty one: the checks below look inside containers that schema.check has already flagged when they are not objects."""
    return x if isinstance(x, dict) else {}


def same_map(a, b) -> bool:
    """Two flat objects with the same keys and equal, non-boolean values (the numbers of the gprobe table)."""
    return isinstance(a, dict) and set(a) == set(b) and all(a[k] == b[k] and not isinstance(a[k], bool) for k in b)


def same_flat(a, b) -> bool:
    """Two flat objects with equal values; a boolean never equals a number (Python's True == 1 would, JavaScript's does not)."""
    return (isinstance(a, dict) and set(a) == set(b)
            and all(a[k] == b[k] and isinstance(a[k], bool) == isinstance(b[k], bool) for k in b))


def version_problems(doc: dict, proto: dict) -> list:
    """v2 adds first-token timing, the suite and the host relation; v1 results must not carry them, and a v2 result must
    declare the closed ones."""
    out = []
    v2 = doc.get("schema") == "strata-bench/v2"
    runner, sizes = view(doc.get("runner")), view(doc.get("sizes"))
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
    suite = proto.get("suite")
    if isinstance(suite, str) and suite in schema.SUITE_BLOCKS and blocks != schema.SUITE_BLOCKS[suite]:
        out.append(f"protocol.suite {suite} lists the blocks " + ", ".join(schema.SUITE_BLOCKS[suite]))
    skipped = proto.get("skipped")
    if skipped is not None and not (isinstance(skipped, list) and all(
            isinstance(k, dict) and k.get("block") in blocks and schema.SKIP_PAIRS.get(k.get("block")) == k.get("reason") for k in skipped)):
        out.append("protocol.skipped must list blocks of this result with their reason: " + ", ".join(f"{b} {r}" for b, r in schema.SKIP_PAIRS.items()))
        skipped = []
    skipped_blocks = [k.get("block") for k in skipped or []]
    validity = view(doc.get("validity"))
    agent, clients = doc.get("agent"), doc.get("clients")
    # per block: (the data a finished result must carry, where that data is, what it is called in the message)
    def n_runs(size):
        return sum(1 for r in runs if r.get("size") == size and r.get("kind") == "run")
    sessions = agent.get("sessions") if isinstance(agent, dict) else None
    counts = [int(n) for n in schema.CLIENTS_PROTOCOL["counts"].split(",")]
    parts = {"agent": (isinstance(sessions, list) and len(sessions) == schema.AGENT_PROTOCOL["sessions"]
                       and n_runs("agent") == schema.AGENT_PROTOCOL["sessions"] * schema.AGENT_PROTOCOL["turns"], "agent" in doc, "agent.sessions"),
             "chat": (isinstance(sizes.get("chat"), dict) and isinstance(sizes["chat"].get("ttft_ms"), dict)
                      and n_runs("chat") == schema.CHAT_PROTOCOL["runs"], "chat" in sizes, "sizes.chat"),
             "clients": (isinstance(clients, list) and [c.get("n") if isinstance(c, dict) else None for c in clients] == counts
                         and all(isinstance(c, dict) and c.get("requests") == c["n"] * schema.CLIENTS_PROTOCOL["rounds"] for c in clients),
                         "clients" in doc, "clients"),
             "first-token": (any(isinstance(sizes.get(z), dict) and isinstance(sizes[z].get("ttft_ms"), dict) for z in schema.TEXT_SIZES)
                             and any(r.get("size") in schema.TEXT_SIZES and isinstance(r.get("stream"), dict) for r in runs), False, "ttft_ms in a text size and its streamed runs")}
    for block, (complete, has_data, label) in parts.items():
        ran = block in blocks and block not in skipped_blocks
        if block != "first-token" and not ran and (has_data or block in proto or any(r.get("size") == block for r in runs)):
            out.append(f"{block} data is only allowed when the {block} block ran")
        elif ran and validity.get("status") != "failed" and not complete:
            out.append(f"a result with the {block} block needs {label}")
        if block != "first-token" and ran and not same_flat(proto.get(block), schema.BLOCK_PROTOCOLS[block]):
            out.append(f"protocol.{block} is not standard-v2's")
    return out


def problems(doc: dict) -> list:
    out = schema.check(doc, schema.PUBLIC)
    if not isinstance(doc, dict):
        return out
    if doc.get("schema") not in schema.SCHEMA_IDS.values():
        out.append("schema is not strata-bench/v1 or strata-bench/v2")
    proto = view(doc.get("protocol"))
    pid = proto.get("id")
    if not (isinstance(pid, str) and pid in schema.SCHEMA_IDS):
        out.append(f"protocol {pid!r} is not public")
    elif doc.get("schema") in schema.SCHEMA_IDS.values() and schema.SCHEMA_IDS[pid] != doc["schema"]:
        out.append(f"protocol {pid} is written in {schema.SCHEMA_IDS[pid]}, not {doc['schema']}")
    out.extend(version_problems(doc, proto))
    known = {**schema.STANDARD_V1_PROMPTS, **(schema.STANDARD_V2_PROMPTS if pid == "standard-v2" else {})}
    for name, h in view(proto.get("prompt_sha256")).items():
        if known.get(name) != h:
            out.append(f"prompt {name} is not {pid}'s")
    from .core import PROBE_SYSTEM, sha256          # the canonical probe system prompt
    if proto.get("probe_system_sha256") not in (None, sha256(PROBE_SYSTEM.encode())):
        out.append("probe system prompt is not standard-v1's")
    from .core import GPROBE_CHARS, GPROBE_SYSTEM   # the canonical gprobe, when the result has one
    gp = view(proto.get("gprobe"))
    if gp and (gp.get("system_sha256") != sha256(GPROBE_SYSTEM.encode())
               or not same_map(gp.get("chars"), {str(k): v for k, v in GPROBE_CHARS.items()})):
        out.append("gprobe is not standard-v1's")
    validity = view(doc.get("validity"))
    status, failure = validity.get("status"), validity.get("failure")
    if status not in ("ok", "warnings", "failed"):
        out.append(f"result is not complete (status {status!r})")
    if status == "failed":                       # the health view: a catastrophic failure with a closed kind and a closed place
        if not isinstance(failure, dict) or failure.get("kind") not in schema.FAILURE_KINDS:
            out.append("a failed result needs failure.kind from " + ", ".join(schema.FAILURE_KINDS))
        elif not (isinstance(failure.get("where"), str) and schema.FAILURE_WHERE.fullmatch(failure["where"])):
            out.append("failure.where is not one of the protocol's call names")
    elif failure is not None:
        out.append("failure is only allowed on a failed result")
    for block, key in (("submission", "by"), ("machine", "machine_id")):      # declared names: short, no free text
        section = doc.get(block)
        if isinstance(section, dict) and section.get(key) is not None and not (
                isinstance(section[key], str) and schema.ID_RE.fullmatch(section[key])):
            out.append(f"{block}.{key} must be a short name (letters, digits, '.', '_' and '-')")
    text = json.dumps(doc, ensure_ascii=False)
    for rx, what in LEAKS:
        m = rx.search(text)
        if m:
            out.append(f"contains {what}: {m.group(0)!r}")
    return out
