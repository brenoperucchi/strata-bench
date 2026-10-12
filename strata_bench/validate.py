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


_SAFE = re.compile(r"[A-Za-z0-9._ -]{0,40}", re.ASCII)


def shown(v) -> str:
    """A value for a message, only when it is short and plain (None, a boolean, a small integer, a short ASCII word). Anything else is not echoed,
    so the two validators cannot differ in how they spell an exotic value."""
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, (int, float)) and float(v).is_integer() and abs(v) < 2 ** 53:      # 1.0 and 1 are the same number in JSON, and in JavaScript
        return str(int(v))
    if isinstance(v, str) and _SAFE.fullmatch(v):
        return "'" + v + "'"
    return "(value not shown)"


def lone_surrogate(x) -> bool:
    """A string or a key anywhere in x with a UTF-16 surrogate that has no partner: it cannot be written as UTF-8, and JavaScript would escape it
    where Python would not, so both refuse it before any text scan."""
    if isinstance(x, str):
        try:
            x.encode("utf-8")
        except UnicodeEncodeError:
            return True
        return False
    if isinstance(x, dict):
        return any(lone_surrogate(k) or lone_surrogate(v) for k, v in x.items())
    if isinstance(x, list):
        return any(lone_surrogate(v) for v in x)
    return False


def tnum(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def ident(*parts):
    """(a, b, ...) when every part is an integer-valued number, else None: how a request names itself (session and turn, round and client, ...)."""
    if all(tnum(p) and float(p).is_integer() for p in parts):
        return tuple(int(p) for p in parts)
    return None


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
    A, C, H = schema.AGENT_PROTOCOL, schema.CLIENTS_PROTOCOL, schema.CHAT_PROTOCOL

    def of(size, kind="run"):
        return [r for r in runs if r.get("size") == size and r.get("kind") == kind]

    def streamed(r):
        st = r.get("stream")
        return isinstance(st, dict) and tnum(st.get("ttft_ms"))

    def exactly(entries, wanted):
        """The runs name exactly the wanted requests, each once (`entries` are the idents of the runs, None when a run does not name itself)."""
        return None not in entries and len(entries) == len(set(entries)) and set(entries) == wanted

    # per block: (the content a finished result must carry, where that content is, what it is called in the message)
    sessions = agent.get("sessions") if isinstance(agent, dict) else None
    counts = [int(n) for n in C["counts"].split(",")]
    agent_ok = (isinstance(sessions, list) and len(sessions) == A["sessions"]
                and all(isinstance(x, dict) and all(k in x for k in schema.AGENT_SESSION_KEYS) for x in sessions)
                and exactly([ident(x.get("index")) for x in sessions], {(i,) for i in range(1, A["sessions"] + 1)})
                and exactly([ident(r.get("index"), r.get("turn")) for r in of("agent")],
                            {(i, t) for i in range(1, A["sessions"] + 1) for t in range(1, A["turns"] + 1)})
                and all(streamed(r) for r in of("agent")))
    chat = sizes.get("chat")
    chat_ok = (isinstance(chat, dict) and isinstance(chat.get("ttft_ms"), dict) and tnum(chat["ttft_ms"].get("median"))
               and exactly([ident(r.get("index")) for r in of("chat")], {(i,) for i in range(1, H["runs"] + 1)}) and all(streamed(r) for r in of("chat")))

    def clients_ok():
        if not (isinstance(clients, list) and len(clients) == len(counts)):
            return False
        pool = of("clients")
        if len(pool) != sum(n * C["rounds"] for n in counts):
            return False
        if any("error_status" in r and (r["error_status"] not in schema.BUSY_STATUS or "stream" in r) for r in runs if r.get("size") == "clients"):    # warm-ups too
            return False
        for row, n in zip(clients, counts):
            mine = [r for r in pool if r.get("clients") == n]
            if not (isinstance(row, dict) and all(k in row for k in schema.CLIENTS_ROW) and row.get("n") == n and row.get("requests") == n * C["rounds"]
                    and exactly([ident(r.get("index"), r.get("client")) for r in mine], {(i, k) for i in range(1, C["rounds"] + 1) for k in range(1, n + 1)})):
                return False
            refused = [r for r in mine if "error_status" in r]
            if any(r["error_status"] not in schema.BUSY_STATUS or "stream" in r for r in refused) or not all(streamed(r) for r in mine if "error_status" not in r):
                return False
            if not (tnum(row.get("errors")) and row["errors"] == len(refused)):
                return False
        return True
    summarized = [z for z in schema.TEXT_SIZES if isinstance(sizes.get(z), dict) and isinstance(sizes[z].get("ttft_ms"), dict)]
    first_ok = bool(summarized) and all(tnum(sizes[z]["ttft_ms"].get("median")) and any(streamed(r) for r in of(z)) for z in summarized)
    parts = {"agent": (agent_ok, "agent" in doc, "agent.sessions"), "chat": (chat_ok, "chat" in sizes, "sizes.chat"),
             "clients": (clients_ok(), "clients" in doc, "clients"), "first-token": (first_ok, False, "ttft_ms in a text size and its streamed runs")}
    for block, (complete, has_data, label) in parts.items():
        ran = block in blocks and block not in skipped_blocks
        if block != "first-token" and not ran and (has_data or block in proto or any(r.get("size") == block for r in runs)):
            out.append(f"{block} data is only allowed when the {block} block ran")
        elif ran and validity.get("status") != "failed" and not complete:
            out.append(f"a result with the {block} block needs {label}")
        if block != "first-token" and ran and not same_flat(proto.get(block), schema.BLOCK_PROTOCOLS[block]):
            out.append(f"protocol.{block} is not standard-v2's")
    if "agent" in skipped_blocks and not (tnum(doc.get("n_ctx")) and doc["n_ctx"] < schema.AGENT_MIN_CTX):
        out.append(f"protocol.skipped agent needs a server context under {schema.AGENT_MIN_CTX} tokens")
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
        out.append(f"protocol {shown(pid)} is not public")
    elif doc.get("schema") in schema.SCHEMA_IDS.values() and schema.SCHEMA_IDS[pid] != doc["schema"]:
        out.append(f"protocol {pid} is written in {schema.SCHEMA_IDS[pid]}, not {doc['schema']}")
    out.extend(version_problems(doc, proto))
    known = {**schema.STANDARD_V1_PROMPTS, **(schema.STANDARD_V2_PROMPTS if pid == "standard-v2" else {})}
    label = pid if isinstance(pid, str) and pid in schema.SCHEMA_IDS else "standard-v1"
    for name, h in schema.ordered(view(proto.get("prompt_sha256"))):
        if known.get(name) != h:
            out.append(f"prompt {name} is not {label}'s")
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
        out.append(f"result is not complete (status {shown(status)})")
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
    if lone_surrogate(doc):
        out.append("contains text that is not valid Unicode")
    else:
        text = json.dumps(doc, ensure_ascii=False)
        for rx, what in LEAKS:
            m = rx.search(text)
            if m:
                out.append(f"contains {what}: {m.group(0)!r}")
    return out
