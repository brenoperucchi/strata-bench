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
    (re.compile(r"\b[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\.(?:" + "|".join("".join(f"[{c.upper()}{c}]" for c in tld) for tld in schema.PRIVATE_TLDS) + r")\b", re.ASCII), "a private hostname"),
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{8,}|hf_[A-Za-z0-9]{8,}|gh[pousr]_[A-Za-z0-9]{8,})", re.ASCII), "a token"),
]


def too_deep(x, limit=schema.MAX_DEPTH) -> bool:
    """True when containers nest more than `limit` levels (checked without recursion, before any walker meets the result)."""
    stack = [(x, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > limit and isinstance(node, (dict, list)):
            return True
        if isinstance(node, dict):
            stack.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list):
            stack.extend((v, depth + 1) for v in node)
    return False


def strings(doc):
    """Every string and every key of the result, decoded, in one fixed order (keys in UTF-16 code unit order, lists in order), without recursion."""
    stack = [doc]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict):
            for k, v in reversed(schema.ordered(node)):
                stack.append(v)
                stack.append(str(k))
        elif isinstance(node, list):
            stack.extend(reversed(node))


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
    if isinstance(v, int):
        return str(v) if abs(v) < 2 ** 53 else "(value not shown)"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() and abs(v) < 2 ** 53 else "(value not shown)"      # 1.0 and 1 are the same number in JSON, and in JavaScript
    if isinstance(v, str) and _SAFE.fullmatch(v):
        return "'" + v + "'"
    return "(value not shown)"


def lone_surrogate(doc) -> bool:
    """A string or a key anywhere in the result with a UTF-16 surrogate that has no partner: it cannot be written as UTF-8, and JavaScript would
    escape it where Python would not, so both refuse it before any text scan."""
    for text in strings(doc):
        try:
            text.encode("utf-8")
        except UnicodeEncodeError:
            return True
    return False


def tnum(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def safe_int(p) -> bool:
    """An integer-valued number JavaScript represents exactly (what a request uses to name itself)."""
    if isinstance(p, bool) or not isinstance(p, (int, float)):
        return False
    if isinstance(p, float) and not p.is_integer():
        return False
    return abs(p) <= 2 ** 53


def ident(*parts):
    """(a, b, ...) when every part is a safe integer, else None: how a request names itself (session and turn, round and client, ...)."""
    return tuple(int(p) for p in parts) if all(safe_int(p) for p in parts) else None


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
        """A finished request: its first-token time, total time, the engine's timings and token counts, and both hashes."""
        st, t, k = r.get("stream"), r.get("timings"), r.get("tokens")
        return (isinstance(st, dict) and tnum(st.get("ttft_ms")) and tnum(r.get("wall_ms"))
                and isinstance(t, dict) and all(tnum(t.get(x)) for x in schema.REQUIRED_TIMINGS)
                and isinstance(k, dict) and tnum(k.get("prompt")) and tnum(k.get("generated"))
                and all(isinstance(r.get(h), str) and schema._HEX.fullmatch(r[h]) for h in ("request_sha256", "output_sha256")))

    def summary(sz, key="ttft_ms"):
        return isinstance(sz, dict) and isinstance(sz.get(key), dict) and tnum(sz[key].get("median"))

    def summaries(sz):
        return all(summary(sz, k) for k in schema.SIZE_SUMMARIES)

    def exactly(entries, wanted):
        """The runs name exactly the wanted requests, each once (`entries` are the idents of the runs, None when a run does not name itself)."""
        return None not in entries and len(entries) == len(set(entries)) and set(entries) == wanted

    # per block: (the content a finished result must carry, where that content is, what it is called in the message)
    sessions = agent.get("sessions") if isinstance(agent, dict) else None
    counts = [int(n) for n in C["counts"].split(",")]
    agent_ok = (isinstance(sessions, list) and len(sessions) == A["sessions"]
                and all(isinstance(x, dict) and all(tnum(x.get(k)) for k in schema.AGENT_SESSION_KEYS) for x in sessions)
                and isinstance(agent.get("turns"), dict) and sorted(agent["turns"]) == [str(t) for t in range(1, A["turns"] + 1)]
                and all(isinstance(v, dict) and all(summary(v, k) for k in ("prompt_n", "cache_n", "ttft_ms")) for v in agent["turns"].values())
                and summary(agent, "hit_pct") and summary(agent, "reread_tokens")
                and exactly([ident(x.get("index")) for x in sessions], {(i,) for i in range(1, A["sessions"] + 1)})
                and exactly([ident(r.get("index"), r.get("turn")) for r in of("agent")],
                            {(i, t) for i in range(1, A["sessions"] + 1) for t in range(1, A["turns"] + 1)})
                and all(streamed(r) for r in of("agent")))
    chat = sizes.get("chat")
    chat_ok = (summaries(chat)
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
                    and tnum(row.get("rounds")) and row["rounds"] == C["rounds"] and tnum(row.get("errors"))
                    and all((tnum(row.get(k)) if row["requests"] - row["errors"] > 0 else row.get(k) is None) for k in schema.CLIENTS_METRICS)
                    and exactly([ident(r.get("index"), r.get("client")) for r in mine], {(i, k) for i in range(1, C["rounds"] + 1) for k in range(1, n + 1)})):
                return False
            refused = [r for r in mine if "error_status" in r]
            if any(r["error_status"] not in schema.BUSY_STATUS or "stream" in r for r in refused) or not all(streamed(r) for r in mine if "error_status" not in r):
                return False
            if not (tnum(row.get("errors")) and row["errors"] == len(refused)):
                return False
        return True
    summarized = [z for z in schema.TEXT_SIZES if isinstance(sizes.get(z), dict) and isinstance(sizes[z].get("ttft_ms"), dict)]
    first_ok = bool(summarized) and all(summaries(sizes[z]) and tnum(proto.get("runs")) and len(of(z)) == proto["runs"] and all(streamed(r) for r in of(z)) for z in summarized)
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


def config_args_problems(doc: dict) -> list:
    """The runner replaces the value of a credential or address flag; a result whose args still carry one is refused."""
    args = view(doc.get("config")).get("args")
    out = []
    if isinstance(args, list):
        for i, a in enumerate(args):
            if isinstance(a, str) and a.startswith("-") and schema.flag_kind(a):
                value = a.split("=", 1)[1] if "=" in a else (args[i + 1] if i + 1 < len(args) and not (isinstance(args[i + 1], str) and args[i + 1].startswith("-")) else None)
                if value is not None and value not in schema.FLAG_PLACEHOLDERS:
                    out.append(f"config.args[{i}] carries a credential or an address")
    return out


def health_problems(doc: dict) -> list:
    """What a result says about its health must agree with itself: a degenerate answer is a failed result, a refusal is a warning, and a status
    and its warnings and error describe the same thing."""
    out = []
    validity = view(doc.get("validity"))
    status, failure, warnings = validity.get("status"), validity.get("failure"), validity.get("warnings")
    runs = [r for r in doc.get("runs") or [] if isinstance(r, dict)] if isinstance(doc.get("runs"), list) else []
    if any(r.get("output_check") == "garbage" for r in runs) and status != "failed":
        out.append("a run with degenerate output needs a failed result")
    if status == "ok" and any("error_status" in r for r in runs):
        out.append("a result with refused requests needs status warnings")
    listed = isinstance(warnings, list) and len(warnings) > 0
    if status == "ok" and listed:
        out.append("status ok cannot carry warnings")
    if status == "warnings" and not listed:
        out.append("status warnings needs at least one warning")
    if validity.get("error") is not None and status != "failed":
        out.append("validity.error is only allowed on a failed result")
    return out


def problems(doc: dict) -> list:
    if too_deep(doc):                            # before any walker: a result nested this deep is not a result, and a recursive check would meet the end of the stack
        return [f"nested more than {schema.MAX_DEPTH} levels deep"]
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
    out.extend(config_args_problems(doc))
    out.extend(health_problems(doc))
    if lone_surrogate(doc):
        out.append("contains text that is not valid Unicode")
    elif any(len(text) > schema.MAX_STRING for text in strings(doc)):                 # declared text is limited: a name, a label, a short note, never a document
        out.append(f"contains a string longer than {schema.MAX_STRING} characters")
    else:
        found = {}
        for text in strings(doc):                # the decoded strings and keys, not their JSON spelling: a newline or a NUL in front of a token hides nothing
            for rx, what in LEAKS:
                if what not in found:
                    m = rx.search(text)
                    if m:
                        found[what] = m.group(0)
        out.extend(f"contains {what}: {found[what]!r}" for _, what in LEAKS if what in found)
    return out


def profile_problems(doc: dict) -> list:
    """A result measured exactly what its profile says: standard-v1 is probe, medium, long and xlong; standard-v2 is the quick, standard or full suite.
    Results are only comparable when everybody measured the same things, so nobody picks sizes. A failed result is a health entry and may stop anywhere."""
    if not isinstance(doc, dict) or view(doc.get("validity")).get("status") == "failed":
        return []
    proto, sizes = view(doc.get("protocol")), view(doc.get("sizes"))
    pid = proto.get("id")
    if pid == "standard-v1":
        want, label = schema.PROFILE_SIZES["standard-v1"], "standard-v1"
    elif pid == "standard-v2":
        suite = proto.get("suite")
        if suite not in ("quick", "standard", "full"):
            return ["protocol.suite must be quick, standard or full: a set of sizes of one's own is not accepted"]
        want, label = schema.PROFILE_SIZES[suite], f"the {suite} suite"
    else:
        return []
    if set(sizes) != set(want):
        return [f"sizes must be exactly {', '.join(want)} for {label} (found {', '.join(sorted(str(k) for k in sizes)) or 'none'})"]
    return []


# Results published before the fixed profiles, with the sizes their contributors picked: sha256 of the file's bytes -> its name. The list is closed and never grows;
# a new result outside the profiles is rejected, whoever sends it.
LEGACY = {
    "f127a866a538fbdd966614c18569a0d542a0d306a273f588c747022fb6c82143":
        "2026-10-11-ryzen9-5090-default-99366938.result.json",
    "1e621fd71e5338a5713742e2f83fcd25b4d2fad6ef9c5d4945107f8a8f6056a1":
        "2026-10-12-ryzen9-5090-0143-1-fork1107-v2-full-custom-1e621fd7.result.json",
    "bcb803a0fd395cefc02bdf4a8d838a0bfe6c0b4bf8f568af32bdc61077aa99ee":
        "2026-10-12-ryzen9-5090-0143-1-v1-f33dcc54.result.json",
    "88d7e7f0f210676f3e5cd4e94f0995b651b78f9f15ef35d51f41bbed053b44cc":
        "2026-10-13-5090-1m-agent-01431-identity-v1.result.json",
    "a66373ae53879f22a68a17f781f0e52bb565e8999bd8c49787b2e4bc2034eeeb":
        "2026-10-13-ryzen9-5090-bat5-B-fork01431-iq3s-v2-a66373ae.result.json",
    "c06b636c2711d70fbe4a19a1d2b74a040785dbb7305a2f7a85c35ef841ee6a0d":
        "2026-10-13-ryzen9-5090-bat5-C-fork0144-xxs-v2-c06b636c.result.json",
    "64b8ff7ff9dff7d5e484acfb5ea1d0367f7cf1d7295bb0cd1df7944bf64f7b60":
        "2026-10-13-ryzen9-5090-bat5-D-fork0144-iq3s-v2-64b8ff7f.result.json",
    "db909690e494a8dfe7e6214286c2d0ebef941c5b2e0e1239e9918707bed81a21":
        "2026-10-13-ryzen9-5090-bat5-E-oficial0144-xxs-v2-db909690.result.json",
    "4649f21492b28070f5ac63e1ff8d124009c3ee2af283fca6571a5a4016c131ec":
        "2026-10-13-v100s-after-calibrate.result.json",
    "4cb18443c99bc5a0a2dd62a92b42a71f45a811b05ae3622cc5f9f23600a3d04a":
        "2026-10-13-v100s-before.result.json",
    "2aea661e3bfec3eed7b480c1245c3ce3669738e1aefa42458a25c59d46c3e1f0":
        "2026-10-14-5090-1m-agent-0144-tuned-v2.result.json",
    "bcfdae7f498a14731094edf76a572d5e69f69c0aa427a4cdb33c3ae4365c4098":
        "2026-10-16-5090-1m-agent-0144-pre441-v1.result.json",
}


def gate(doc: dict, legacy: bool = False) -> list:
    """The export gate of the dashboard: the data rules, then the profile. A legacy file keeps only the data rules."""
    return problems(doc) + ([] if legacy else profile_problems(doc))
