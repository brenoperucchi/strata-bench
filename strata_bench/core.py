"""Measurement core: preflight, calls, per-call persistence, result.json (schema strata-bench/v1).

Attach mode only: the caller starts and stops the server. Standard library only.
"""
import hashlib
import ipaddress
import http.client
import json
import os
import re
import socket
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from . import SCHEMA, SCHEMA_V2, VERSION
from .schema import (AGENT_MIN_CTX, AGENT_PROTOCOL, BLOCK_PROTOCOLS, BUSY_STATUS, CHAT_PROTOCOL, CLIENTS_PROTOCOL, FAILURE_KINDS, HOST_RELATIONS, ID_RE, IDENTITY, OBSERVED, PUBLIC, RUN, SERVER_FIELDS,
                     SHARED_KEYS, STANDARD_V1_PROMPTS, STANDARD_V2_PROMPTS, prune)

PROMPTS = Path(__file__).parent / "prompts"
SIZES = ("short", "medium", "long", "xlong")
PROTOCOL = "standard-v1"
PROTOCOL_V2 = "standard-v2"
LOOPBACK = ("localhost",)
MEASURED = SIZES + ("chat", "agent", "clients")      # what --sizes may name besides the probes; the last three are standard-v2 blocks
REUSE_LIMIT = 64          # more reused prompt tokens than this on a size run means the prompt was not read fresh
SECRET_ENV = re.compile(r"(?i)(api_?key|secret|passw(or)?d|token)$")

PROBE_SYSTEM = ("You are a code reviewer for a C++ inference engine. Classify each line the user sends. Answer with "
                "exactly one word: SAFE if the line has no obvious correctness risk, RISKY if it does. Rules: " +
                " ".join(f"Rule {i}: " + r for i, r in enumerate([
                    "atomics with relaxed ordering that publish data to another thread are RISKY",
                    "unchecked integer narrowing from 64-bit to 32-bit sizes is RISKY",
                    "pointer arithmetic past the end of a buffer is RISKY",
                    "a lock acquired without a matching release on every path is RISKY",
                    "plain arithmetic, logging, comments and declarations are SAFE",
                    "reads of constants or configuration values are SAFE",
                    "a busy-wait loop without a pause or yield instruction is RISKY",
                    "a division whose divisor can be zero is RISKY",
                    "a function call whose error result is ignored is RISKY",
                    "standard containers used within their size are SAFE"], 1)) +
                " Think about memory ordering, bounds, error handling and thread safety, but never explain: one word "
                "only. " * 3)
PROBE_CALLS, PROBE_SKIP = 30, 2
# gprobe (opt-in, --sizes ...,gprobe): short-request probes shaped like an agent's classifier calls - a short fixed
# system prompt and a ~150/300/450-token excerpt of the public xlong.txt, a different excerpt per call, 1 token out.
# It measures the prompt path below Strata's 1024-token streamed walk, which the line probes barely touch.
GPROBE_SYSTEM = ("You review one excerpt of C++ from an inference engine. Answer with exactly one word: SAFE if the "
                 "excerpt has no obvious correctness risk, RISKY if it does. Risky means a data race, an out-of-bounds "
                 "access, an ignored error or an integer overflow. One word only, no explanation.")
GPROBE_CHARS = {150: 525, 300: 1050, 450: 1575}   # ~3.5 characters per token for this code


class PreflightError(Exception):
    pass


class MeasureFailure(Exception):
    """A catastrophic failure of the SERVER during a measurement (health view): kind is one of schema.FAILURE_KINDS."""

    def __init__(self, kind: str, where=None, http_status=None):
        super().__init__(kind)
        self.kind, self.where, self.http_status = kind, where, http_status


FAILURE_TEXT = {"crash": "the server closed or refused the connection during the run",
                "oom": "the server reported that it ran out of memory",
                "garbage": "the server returned degenerate output (a single repeated token, or no letters or digits)",
                "hang": "a request did not finish in time",
                "server_error": "the server answered with an HTTP 5xx error"}
OOM_RX = re.compile(r"out of memory|\boom\b|cudamalloc|failed to allocate|allocation failed|cannot allocate", re.I)


def classify_failure(e: BaseException) -> tuple:
    """(kind, http_status) for an exception raised by a call, or (None, status) when it is not a server health failure (a client-side problem, a missing field, ...)."""
    if isinstance(e, MeasureFailure):
        return e.kind, e.http_status
    if isinstance(e, urllib.error.HTTPError):
        try:
            body = e.read(2048).decode("utf-8", "replace")
        except Exception:
            body = ""
        if OOM_RX.search(body):
            return "oom", e.code
        return ("server_error", e.code) if e.code >= 500 else (None, e.code)
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "hang", None
    if isinstance(e, urllib.error.URLError):
        return (("hang" if isinstance(e.reason, (socket.timeout, TimeoutError)) else "crash" if isinstance(e.reason, (ConnectionError, OSError)) else None), None)
    if isinstance(e, (ConnectionError, http.client.RemoteDisconnected, http.client.IncompleteRead, http.client.BadStatusLine)):
        return "crash", None
    return None, None


def looks_like_garbage(text: str) -> bool:
    """Degenerate output: at least 40 words of which under 5% are distinct, or a long text with no letter or digit at all."""
    words = text.split()
    if len(words) >= 40 and len(set(words)) / len(words) < 0.05:
        return True
    return len(text) >= 80 and not any(c.isalnum() for c in text)


class MeasureError(Exception):
    pass


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def is_path(v: str) -> bool:
    return "\\" in v or "/" in v or v.lower().endswith((".gguf", ".bin", ".exe", ".json"))


def secret_env(k: str) -> bool:
    """Credentials in env: STRATA_API_KEY, and outside the engine's STRATA_ prefix any *_TOKEN / *_API_KEY /
    *_SECRET / *_PASSWORD (HF_TOKEN, OPENAI_API_KEY). Engine knobs like STRATA_INDEXER_PER_TOKEN stay."""
    return k.upper() == "STRATA_API_KEY" or (not k.upper().startswith("STRATA_") and bool(SECRET_ENV.search(k)))


def depath(v):
    if isinstance(v, dict):
        return {k: depath(x) for k, x in sorted(v.items())}
    if isinstance(v, list):
        return [depath(x) for x in v]
    return "<path>" if isinstance(v, str) and is_path(v) else v


def scrub_config(cfg: dict) -> dict:
    """args, env and the server fields a start uses, with every path replaced by the role of the flag that carries
    it (<pack>, <native>, ...) and every secret-looking value redacted. host, port, log, exe and api_key stay out."""
    args, out = [str(x) for x in cfg.get("args", [])], []
    for i, a in enumerate(args):
        prev = args[i - 1] if i else ""
        out.append(f"<{prev.lstrip('-')}>" if is_path(a) and prev.startswith("--") else "<path>" if is_path(a) else a)
    env = {k: "<redacted>" if secret_env(k) else depath(str(v)) for k, v in sorted((cfg.get("env") or {}).items())}
    server = prune(depath({k: cfg[k] for k in SERVER_FIELDS if k in cfg}), PUBLIC["config"]["server"])
    return {"args": out, "env": env, "server": server}


def config_block(cfg: dict, origin: str, verified: bool, shared=None) -> dict:
    s = scrub_config(cfg)
    if shared is not None:
        s["server_shared"] = shared
    return {**s, "config_id": sha256(json.dumps(s, sort_keys=True).encode())[:16], "config_origin": origin,
            "config_origin_verified": verified}


def flag(cfg: dict, name: str):
    a = cfg.get("args", [])
    return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else None


API_KEY = {"value": None}     # set by run(); sent as a bearer token, never written


def http_json(url: str, body=None, timeout=1800):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"content-type": "application/json"}
    if API_KEY["value"]:
        headers["authorization"] = "Bearer " + API_KEY["value"]
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def basename(p) -> str:
    return Path(str(p or "").replace("\\", "/")).name


def preflight(base: str, cfg: dict) -> tuple:
    """The server must be the config's: same model file and same context, else nothing is measured."""
    try:
        props = http_json(base + "/props", timeout=30)
    except ValueError as e:
        raise PreflightError(f"/props is not JSON: {e}")
    warnings = []
    if not isinstance(props.get("build_info"), str) or not props.get("build_info"):
        raise PreflightError("/props has no build_info: the engine version is unknown")
    if not props.get("model_path"):
        raise PreflightError("/props has no model_path: the model is unknown")
    served = basename(props.get("model_path"))
    want = flag(cfg, "--native")
    if want and served != basename(want):
        raise PreflightError(f"server model {served!r} is not the config's {basename(want)!r}")
    if not want:
        warnings.append("model check skipped: no --native in the config")
    n_ctx = (props.get("default_generation_settings") or {}).get("n_ctx")
    want_ctx = flag(cfg, "--max-context")
    if want_ctx:
        try:
            ok = int(n_ctx) == int(want_ctx) and int(n_ctx) > 0
        except (TypeError, ValueError):
            ok = False
        if not ok:
            raise PreflightError(f"server context {n_ctx!r} is not the config's {want_ctx}")
    try:                                        # a server with an api_key refuses /v1 without it
        http_json(base + "/v1/models", timeout=30)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise PreflightError("the server wants an API key (--api-key or STRATA_API_KEY)")
    shared = None
    try:                                        # chat settings the server applies to requests that omit them
        s = http_json(base + "/settings", timeout=30)
        shared = prune({k: v for k, v in (s.get("defaults") or {}).items() if k in SHARED_KEYS},
                       PUBLIC["config"]["server_shared"])
        for k in ("reasoning_effort", "experimental_speed_projection"):
            if shared.get(k) not in (None, False, ""):
                warnings.append(f"server shared setting {k}={shared[k]!r} applies to the size runs")
    except (urllib.error.HTTPError, ValueError):
        warnings.append("server shared settings unknown (no /settings)")
    slots = props.get("total_slots")
    return {"build_info": props.get("build_info"), "model_file": served, "n_ctx": n_ctx, "shared": shared,
            "slots": slots if isinstance(slots, int) and not isinstance(slots, bool) else None}, warnings


def check_prompts(v2=False) -> dict:
    """Every prompt file must match SHA256SUMS byte for byte (a CRLF checkout or an edit is a different protocol)."""
    sums = {**STANDARD_V1_PROMPTS, **(STANDARD_V2_PROMPTS if v2 else {})}   # pinned in the code: an edited SHA256SUMS does not change the protocol
    for name, h in sums.items():
        got = sha256((PROMPTS / name).read_bytes())
        if got != h:
            raise PreflightError(f"prompt {name} does not match SHA256SUMS ({got[:12]} != {h[:12]}): "
                                 f"not {PROTOCOL_V2 if v2 else PROTOCOL} (a CRLF checkout?)")
    return sums


def call(base: str, messages: list, max_tokens: int, extra=None) -> dict:
    body = {"messages": messages, "temperature": 0, "seed": 42, "max_tokens": max_tokens, **(extra or {})}
    t0 = time.perf_counter()
    d = http_json(base + "/v1/chat/completions", body)
    wall = time.perf_counter() - t0
    ch = d["choices"][0]
    msg, finish = ch.get("message", {}), ch.get("finish_reason")
    t = d.get("timings") or {}
    return {"request_sha256": sha256(json.dumps(messages, ensure_ascii=False).encode()),
            "wall_ms": round(wall * 1000, 1), "timings": prune(t, RUN["timings"]),
            "usage": prune(d.get("usage"), RUN["usage"]),
            "tokens": {"prompt": t.get("prompt_n"), "reused": t.get("cache_n"), "generated": t.get("predicted_n")},
            "reasoning_chars": len(msg.get("reasoning_content") or ""), "content_chars": len(msg.get("content") or ""),
            "finish_reason": finish,
            "output_check": "garbage" if looks_like_garbage((msg.get("reasoning_content") or "") + " " + (msg.get("content") or "")) else "ok",
            "output_sha256": sha256(json.dumps([msg.get("reasoning_content") or "", msg.get("content") or "",
                                                finish]).encode())}


def percentile(sorted_values: list, pct: int):
    """Nearest rank: the smallest value with at least pct percent of the sample at or below it."""
    return sorted_values[max(1, -(-pct * len(sorted_values) // 100)) - 1]


def stream_error_kind(ev: dict) -> str:
    return "oom" if OOM_RX.search(json.dumps(ev.get("error"))[:2048]) else "server_error"


def call_stream(base: str, messages: list, max_tokens: int, extra=None, sink=None) -> dict:
    """standard-v2 request: the same body as call() but streamed (SSE). Time to first token runs from just before the
    request is sent to the first non-empty piece of content or reasoning_content (keep-alives and empty deltas do not
    count), so it includes the network. Speeds still come from the engine's own `timings` in the stream's last chunk.
    `sink`, when given, receives the answer text (sink["content"]) for a caller that must send it back; it is never written to a result."""
    body = {"messages": messages, "temperature": 0, "seed": 42, "max_tokens": max_tokens, "stream": True,
            "stream_options": {"include_usage": True}, "reasoning_effort": "none",
            "chat_template_kwargs": {"enable_thinking": False}, **(extra or {})}
    headers = {"content-type": "application/json"}
    if API_KEY["value"]:
        headers["authorization"] = "Bearer " + API_KEY["value"]
    req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(body).encode(), headers=headers)
    content, reasoning, stamps, finish, timings, usage, done = [], [], [], None, {}, None, False
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=1800) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):           # blank separators, ": keep-alive" comments, event names
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                done = True
                break
            ev = json.loads(payload)
            if not isinstance(ev, dict):
                continue
            if "error" in ev:                          # the server failed mid-stream; its own words are never kept
                raise MeasureFailure(stream_error_kind(ev))
            now = time.perf_counter()
            for ch in ev.get("choices") or []:
                delta = ch.get("delta") or {}
                c, rc = delta.get("content") or "", delta.get("reasoning_content") or ""
                if c or rc:
                    stamps.append(now)
                    content.append(c)
                    reasoning.append(rc)
                finish = ch.get("finish_reason") or finish
            if isinstance(ev.get("timings"), dict):
                timings = ev["timings"]
            if isinstance(ev.get("usage"), dict):
                usage = ev["usage"]
    wall = time.perf_counter() - t0
    if not done and finish is None:                    # the stream ended with no finish_reason and no [DONE]: the server died
        raise MeasureFailure("crash")
    text, rtext = "".join(content), "".join(reasoning)
    if sink is not None:
        sink["content"] = text
    gaps = sorted(round((b - a) * 1000, 1) for a, b in zip(stamps, stamps[1:]))
    stream = {"ttft_ms": round((stamps[0] - t0) * 1000, 1) if stamps else None, "total_ms": round(wall * 1000, 1),
              "pieces": len(stamps),
              "gap_ms": {"p50": percentile(gaps, 50), "p95": percentile(gaps, 95), "max": gaps[-1]} if gaps else None}
    return {"request_sha256": sha256(json.dumps(messages, ensure_ascii=False).encode()),
            "wall_ms": stream["total_ms"], "timings": prune(timings, RUN["timings"]),
            "usage": prune(usage, RUN["usage"]),
            "tokens": {"prompt": timings.get("prompt_n"), "reused": timings.get("cache_n"),
                       "generated": timings.get("predicted_n")},
            "reasoning_chars": len(rtext), "content_chars": len(text), "finish_reason": finish,
            "output_check": "garbage" if looks_like_garbage(rtext + " " + text) else "ok",
            "output_sha256": sha256(json.dumps([rtext, text, finish]).encode()), "stream": stream}


def require(r: dict, keys: tuple, what: str):
    missing = [k for k in keys if not isinstance(r["timings"].get(k), (int, float))]
    if "stream" in r and not r["timings"]:
        raise MeasureError(f"{what}: the server sent no timings in the stream (no_stream_timings)")
    if "stream" in r and not isinstance((r["stream"] or {}).get("ttft_ms"), (int, float)):
        raise MeasureError(f"{what}: the stream carried no text, so there is no first token")
    if missing:
        raise MeasureError(f"{what}: the server returned no {', '.join(missing)}")


def summary(runs: list, key: str):
    v = [r["timings"][key] for r in runs if isinstance(r["timings"].get(key), (int, float))]
    return {"median": statistics.median(v), "min": min(v), "max": max(v)} if v else None


def spread(values: list):
    v = [x for x in values if isinstance(x, (int, float)) and not isinstance(x, bool)]
    return {"median": statistics.median(v), "min": min(v), "max": max(v)} if v else None


def session_stats(index: int, rows: list) -> dict:
    """What the cache saved over one agent session. Before turn t the server has seen `prior` tokens (the turn before it plus
    its answer); `cache_n` of them should come back from the cache, so `prior - cache_n` were read again. Turn 1 has no past."""
    prior = hit = seen = reread = new = 0
    for t, r in enumerate(rows, 1):
        read, cache = r["tokens"]["prompt"] or 0, r["tokens"]["reused"] or 0
        total = read + cache
        if t > 1:
            hit += min(cache, prior)
            seen += prior
            reread += max(0, prior - cache)
            new += max(0, total - prior)
        prior = total + (r["tokens"]["generated"] or 0)
    return {"index": index, "hit_pct": round(100 * hit / seen, 1) if seen else None, "reread_tokens": reread, "new_tokens": new}


def stream_summary(runs: list, key: str):
    v = [r["stream"][key] for r in runs if isinstance((r.get("stream") or {}).get(key), (int, float))]
    return {"median": statistics.median(v), "min": min(v), "max": max(v)} if v else None


def is_loopback(base: str) -> bool:
    host = (urllib.parse.urlparse(base).hostname or "").lower()
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:                          # a name, not an address
        return host in LOOPBACK


def core_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).parent,
                              capture_output=True, text=True, timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def merge_identity(block: str, observed: dict, ident: dict, warnings: list) -> dict:
    """Identity adds declared facts (hashes, hardware); it never overrides what the server reported, and keys outside
    the public schema are dropped."""
    out = dict(observed)
    spec = IDENTITY.get(block, {})
    for k, v in (ident.get(block) or {}).items():
        if k not in spec:
            warnings.append(f"identity {block}.{k} dropped: not in the public schema")
            continue
        clean = prune(v, spec[k])
        if clean != v:
            warnings.append(f"identity {block}.{k}: parts outside the public schema dropped")
        v = clean
        if k in OBSERVED.get(block, set()):
            if v != observed.get(k):
                warnings.append(f"identity {block}.{k}={v!r} differs from the server's {observed.get(k)!r}: kept the "
                                "server's")
        else:
            out[k] = v
    return out


class Result:
    """The result.json, rewritten after every call (atomic replace), so a failure keeps what was measured."""

    def __init__(self, path: Path, doc: dict):
        self.path, self.doc, self.where = path, doc, None         # `where` = the call in flight (failure.where, if it fails)

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.doc, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)


def run(base: str, cfg: dict, origin: str, sizes: list, label: str, out_dir: Path, identity=None,
        runs_n=3, warmup=1, max_tokens=256, probe_offset=0, api_key=None, by=None, protocol=PROTOCOL,
        host_relation=None, log=print) -> int:
    if not re.fullmatch(r"setup-default|custom-\d+", origin):
        raise SystemExit("--origin must be setup-default or custom-N")
    v2 = protocol == PROTOCOL_V2
    if protocol not in (PROTOCOL, PROTOCOL_V2):
        raise SystemExit(f"unknown protocol {protocol!r}")
    if v2:                                      # first-token time includes the network, so say where the client ran
        if host_relation is None and is_loopback(base):
            host_relation = "same-host"
        if host_relation not in HOST_RELATIONS:
            raise SystemExit("--host-relation " + "|".join(HOST_RELATIONS) + " is required with standard-v2 when --base is not a loopback address")
    elif host_relation is not None:
        raise SystemExit("--host-relation is only for standard-v2")
    if not v2 and any(x in sizes for x in ("chat", "agent", "clients")):
        raise SystemExit("the chat, agent and clients blocks are only for --protocol standard-v2")
    if v2 and not any(x in MEASURED for x in sizes):
        raise SystemExit("--protocol standard-v2 needs at least one of " + ", ".join(MEASURED) + " in --sizes")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{label}.result.json"
    if path.exists():
        log(f"refusing to overwrite {path}")
        return 2
    API_KEY["value"] = api_key
    try:
        sums = check_prompts(v2)
        server, warnings = preflight(base, cfg)
    except (PreflightError, OSError) as e:
        log(f"preflight refused: {e}")
        return 2
    requested, skipped = list(sizes), []
    if "agent" in sizes:                        # a block the server cannot run is skipped with a closed reason, not failed
        try:
            small = int(server["n_ctx"]) < AGENT_MIN_CTX
        except (TypeError, ValueError):
            small = True
        if small:
            skipped.append({"block": "agent", "reason": "context_too_small"})
            sizes = [x for x in sizes if x != "agent"]
            log(f"agent skipped: the server context {server['n_ctx']!r} is under {AGENT_MIN_CTX} tokens")
    if "clients" in sizes and server["slots"] is not None and server["slots"] < 2:
        skipped.append({"block": "clients", "reason": "no_parallel_slots"})
        sizes = [x for x in sizes if x != "clients"]
        log(f"clients skipped: the server reports {server['slots']} slot, so it cannot serve clients at once")
    if v2 and not any(x in MEASURED for x in sizes):
        log("nothing left to measure")
        return 2
    blocks = (["first-token"] if any(x in SIZES for x in requested) else []) + [b for b in ("chat", "agent", "clients") if b in requested]
    ident = identity or {}
    by = by if by is not None else (ident.get("submission") or {}).get("by")
    for what, v in (("--by", by), ("machine.machine_id", (ident.get("machine") or {}).get("machine_id"))):
        if v is not None and not (isinstance(v, str) and ID_RE.fullmatch(v)):
            raise SystemExit(f"{what} must be a short name: letters, digits, '.', '_' and '-' (up to 39 characters)")
    verified = bool((ident.get("config") or {}).get("origin_verified")) and origin == "setup-default"
    res = Result(path, {
        "schema": SCHEMA_V2 if v2 else SCHEMA,
        # flat copies for dashboards that read the legacy run_bench.py layout (no paths: model_path is the file name)
        "label": label, "build_info": server["build_info"], "model_path": server["model_file"],
        "n_ctx": server["n_ctx"],
        "runner": {"name": "strata-bench", "version": VERSION, "commit": core_commit(),
                   **({"host_relation": host_relation} if v2 else {})},
        "submission": {"label": label, "date": datetime.now().astimezone().isoformat(timespec="seconds"),
                       **({"by": by} if by else {})},
        "machine": merge_identity("machine", {}, ident, warnings),
        "engine": merge_identity("engine", {"build_info": server["build_info"]}, ident, warnings),
        "model": merge_identity("model", {"file": server["model_file"], "n_ctx": server["n_ctx"]}, ident, warnings),
        "config": {**config_block(cfg, origin, verified, server["shared"]),
                   **({"calibration": merge_identity("calibration", {}, ident, warnings)}
                      if ident.get("calibration") else {})},
        "protocol": {"id": protocol, "prompt_sha256": sums, "probe_system_sha256": sha256(PROBE_SYSTEM.encode()),
                     "sampling": {"temperature": 0, "seed": 42, "cache_prompt": "false on sizes"},
                     "max_tokens": max_tokens, "warmup": warmup, "runs": runs_n,
                     "probe": {"calls": PROBE_CALLS, "skip_first": PROBE_SKIP, "max_tokens": 1, "offset": probe_offset,
                               "cache_prompt": "server default (the shared system prefix may be reused)"},
                     "reasoning_sent": {"sizes": "off (reasoning_effort none, enable_thinking false)" if v2 else "server default",
                                        "probe": "enable_thinking false"},
                     "unique_line": "[strata-bench <size> <warmup|run> <i>]",
                     **({"suite": "custom", "blocks": blocks, **({"skipped": skipped} if skipped else {}),
                         **{b: dict(BLOCK_PROTOCOLS[b]) for b in BLOCK_PROTOCOLS if b in sizes}} if v2 else {})},
        "runs": [], "sizes": {},
        "validity": {"status": "running", "warnings": warnings},
    })
    d = res.doc
    res.save()
    try:
        for size in sizes:
            if size == "probe":
                measure_probe(base, res, probe_offset, log)
                continue
            if size == "agent":
                measure_agent(base, res, log)
                continue
            if size == "clients":
                measure_clients(base, res, log)
                continue
            if size == "gprobe":
                d["protocol"]["gprobe"] = {"system_sha256": sha256(GPROBE_SYSTEM.encode()), "chars": GPROBE_CHARS,
                                           "source": "xlong.txt", "calls": PROBE_CALLS, "skip_first": PROBE_SKIP,
                                           "max_tokens": 1}
                measure_gprobe(base, res, log)
                continue
            text = (PROMPTS / ("short.txt" if size == "chat" else f"{size}.txt")).read_text(encoding="utf-8")
            done = []
            n_warm, n_runs, cap = (warmup, runs_n, max_tokens) if size != "chat" else (
                CHAT_PROTOCOL["warmup"], CHAT_PROTOCOL["runs"], CHAT_PROTOCOL["max_tokens"])
            for kind, n in (("warmup", n_warm), ("run", n_runs)):
                for i in range(1, n + 1):
                    msg = [{"role": "user", "content": f"[strata-bench {size} {kind} {i}]\n" + text}]
                    res.where = f"{size} {kind} {i}"
                    r = {"size": size, "kind": kind, "index": i,
                         **(call_stream if v2 else call)(base, msg, cap, {"cache_prompt": False})}
                    d["runs"].append(r)
                    res.save()
                    if r.get("output_check") == "garbage":
                        raise MeasureFailure("garbage", res.where)
                    if kind != "run":
                        continue
                    require(r, ("prompt_n", "prompt_ms", "prompt_per_second", "predicted_n", "predicted_per_second"),
                            f"{size} run {i}")
                    done.append(r)
                    reused = r["tokens"]["reused"]
                    if reused is None:
                        d["validity"]["warnings"].append(f"{size} run {i}: reused tokens unknown (no cache_n)")
                    elif reused > REUSE_LIMIT:
                        d["validity"]["warnings"].append(f"{size} run {i}: {reused} prompt tokens reused, the prompt "
                                                         "was not read fresh")
                    t = r["timings"]
                    log(f"{label} {size} run {i}: prompt {t.get('prompt_n')} (+{t.get('cache_n')} reused) "
                        f"{t.get('prompt_per_second')} tok/s, decode {t.get('predicted_n')} tok "
                        f"{t.get('predicted_per_second')} tok/s" + (f", first token {r['stream']['ttft_ms']} ms" if v2 else ""))
            d["sizes"][size] = {"prompt_tps": summary(done, "prompt_per_second"),
                                "decode_tps": summary(done, "predicted_per_second"),
                                "prompt_ms": summary(done, "prompt_ms"),
                                **({"ttft_ms": stream_summary(done, "ttft_ms")} if v2 else {})}
            res.save()
    except KeyboardInterrupt:                   # Ctrl+C keeps the partial result too, and never leaves it as "running"
        d["validity"].update(status="incomplete", error="interrupted by the user")
        res.save()
        log("incomplete: interrupted by the user")
        return 3
    except Exception as e:                      # keep what was measured, say why it stopped
        kind, http_status = classify_failure(e)
        if kind in FAILURE_KINDS:               # the SERVER failed (health view): a closed kind, a closed place, a fixed text (never the server's own words)
            d["validity"].update(status="failed", error=FAILURE_TEXT[kind],
                                 failure={"kind": kind, "where": getattr(e, "where", None) or res.where, "http_status": http_status})
            res.save()
            log(f"failed ({kind}) at {d['validity']['failure']['where']}")
            return 3
        d["validity"].update(status="incomplete", error=f"{type(e).__name__}: {e}"[:300])
        res.save()
        log(f"incomplete: {e}")
        return 3
    d["validity"]["status"] = "ok" if not d["validity"]["warnings"] else "warnings"
    res.save()
    log(f"written {path}")
    return 0


def measure_probe(base: str, res: Result, offset: int, log):
    """Classification probes: one fixed system prompt, one public code line per call, 1-token answer; the first
    PROBE_SKIP calls warm the shared system prefix and stay out of the summary."""
    d = res.doc
    lines = [l.strip() for l in (PROMPTS / "probe_lines.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = []
    for i in range(PROBE_CALLS):
        msg = [{"role": "system", "content": PROBE_SYSTEM}, {"role": "user", "content": lines[(offset + i) % len(lines)]}]
        res.where = f"probe {i + 1}"
        r = {"size": "probe", "kind": "warmup" if i < PROBE_SKIP else "run", "index": i + 1,
             **call(base, msg, 1, {"chat_template_kwargs": {"enable_thinking": False}})}
        d["runs"].append(r)
        res.save()
        if i >= PROBE_SKIP:
            require(r, ("prompt_n", "prompt_ms"), f"probe {i + 1}")
            p, c = r["tokens"]["prompt"], r["tokens"]["reused"]
            if p is not None and p <= 4:          # prompt_n is what was read: ~nothing read = the whole call reused
                d["validity"]["warnings"].append(f"probe {i + 1}: only {p} prompt tokens read ({c} reused), the "
                                                 "whole call came from the cache")
        rows.append(r)
    kept = rows[PROBE_SKIP:]
    reused = [r["tokens"]["reused"] for r in kept if r["tokens"]["reused"] is not None]
    d["sizes"]["probe"] = {"prompt_ms": summary(kept, "prompt_ms"),
                           "wall_ms": {"median": statistics.median(r["wall_ms"] for r in kept)},
                           "reused_median": statistics.median(reused) if reused else None}
    s = d["sizes"]["probe"]
    log(f"probe: prompt_ms median {s['prompt_ms']['median']}, reused median {s['reused_median']}")


def measure_agent(base: str, res: Result, log):
    """standard-v2 agent block: one conversation of AGENT_PROTOCOL["turns"] turns. Each turn adds one public source file and a question to the
    history (system text and four tool definitions first), the server's own answer goes back into the history, and the server's prompt cache is
    on, so every turn after the first should read only what is new. One warm-up session, then AGENT_PROTOCOL["sessions"] measured ones; each
    session starts with its own marker line so sessions never share a cache. Tools are offered with tool_choice none and never executed."""
    d, P = res.doc, AGENT_PROTOCOL
    system = (PROMPTS / "agent_system.txt").read_text(encoding="utf-8")
    tools = json.loads((PROMPTS / "agent_tools.json").read_text(encoding="utf-8"))
    script = json.loads((PROMPTS / "agent_turns.json").read_text(encoding="utf-8"))
    docs = [(PROMPTS / f"agent_doc_{i}.txt").read_text(encoding="utf-8") for i in range(1, P["turns"] + 1)]
    sessions = []
    for kind, n in (("warmup", P["warmup"]), ("run", P["sessions"])):
        for i in range(1, n + 1):
            messages, rows = [{"role": "system", "content": f"[strata-bench agent {kind} {i}]\n" + system}], []
            for t in range(1, P["turns"] + 1):
                messages.append({"role": "user", "content": script["template"].format(
                    n=t, doc=docs[t - 1], question=script["questions"][t - 1])})
                res.where = f"agent {kind} {i} turn {t}"
                sink = {}
                r = {"size": "agent", "kind": kind, "index": i, "turn": t,
                     **call_stream(base, messages, P["max_tokens"], {"cache_prompt": True, "tools": tools, "tool_choice": "none"}, sink)}
                d["runs"].append(r)
                res.save()
                if r.get("output_check") == "garbage":
                    raise MeasureFailure("garbage", res.where)
                if kind == "run":
                    require(r, ("prompt_n", "cache_n", "prompt_ms", "prompt_per_second", "predicted_n", "predicted_per_second"), res.where)
                messages.append({"role": "assistant", "content": sink.get("content", "")})
                rows.append(r)
                tk = r["tokens"]
                log(f"agent {kind} {i} turn {t}: read {tk['prompt']} (+{tk['reused']} from cache), first token {r['stream']['ttft_ms']} ms, "
                    f"decode {r['timings'].get('predicted_per_second')} tok/s")
            if kind == "run":
                st = session_stats(i, rows)
                sessions.append(st)
                if st["hit_pct"] is not None and st["hit_pct"] < 50:
                    d["validity"]["warnings"].append(f"agent run {i}: only {st['hit_pct']}% of the earlier conversation came from the cache")
                log(f"agent session {i}: cache hit {st['hit_pct']}%, {st['reread_tokens']} tokens read again")
    measured = [r for r in d["runs"] if r["size"] == "agent" and r["kind"] == "run"]
    turns = {}
    for t in range(1, P["turns"] + 1):
        rs = [r for r in measured if r["turn"] == t]
        turns[str(t)] = {"prompt_n": spread([r["tokens"]["prompt"] for r in rs]), "cache_n": spread([r["tokens"]["reused"] for r in rs]),
                         "predicted_n": spread([r["tokens"]["generated"] for r in rs]),
                         "ttft_ms": spread([r["stream"]["ttft_ms"] for r in rs]), "prompt_ms": spread([r["timings"].get("prompt_ms") for r in rs]),
                         "decode_tps": spread([r["timings"].get("predicted_per_second") for r in rs])}
    d["agent"] = {"sessions": sessions, "hit_pct": spread([x["hit_pct"] for x in sessions]),
                  "reread_tokens": spread([x["reread_tokens"] for x in sessions]), "turns": turns}
    res.save()


def client_request(base: str, messages: list, results: list, k: int, barrier: threading.Barrier):
    """One client of the clients block, in its own thread: wait for the others, send one streamed request, store the outcome in results[k]."""
    try:
        barrier.wait(timeout=60)
        results[k] = ("ok", call_stream(base, messages, CLIENTS_PROTOCOL["max_tokens"], {"cache_prompt": False}))
    except urllib.error.HTTPError as e:
        results[k] = ("busy", e.code) if e.code in BUSY_STATUS else ("fail", e)
    except Exception as e:                                  # a closed connection, a timeout, a bad stream: judged by the caller
        results[k] = ("fail", e)


def measure_clients(base: str, res: Result, log):
    """standard-v2 clients block: for each N in CLIENTS_PROTOCOL["counts"], N clients send the same public prompt at once (each with its own first line,
    so none reads another's cache), 1 warm-up round and CLIENTS_PROTOCOL["rounds"] measured ones. A client the server turns away as busy (429 or 503) is
    counted in `errors`; any other failure of a request is a failure of the server and stops the run. total_tps is the tokens all clients
    generated divided by the time from the common start to the last finish."""
    d, P = res.doc, CLIENTS_PROTOCOL
    text = (PROMPTS / "medium.txt").read_text(encoding="utf-8")
    rows = []
    for n in (int(x) for x in P["counts"].split(",")):
        ok, ttft, latency, tps, errors, requests = [], [], [], [], 0, 0
        for kind, rounds in (("warmup", P["warmup"]), ("run", P["rounds"])):
            for rnd in range(1, rounds + 1):
                res.where = f"clients {n} {'warmup' if kind == 'warmup' else 'round'} {rnd}"
                results, barrier = [None] * n, threading.Barrier(n + 1)
                threads = [threading.Thread(target=client_request, daemon=True, args=(
                    base, [{"role": "user", "content": f"[strata-bench clients {n} {kind} {rnd} {k + 1}]\n" + text}], results, k, barrier))
                    for k in range(n)]
                for t in threads:
                    t.start()
                try:
                    barrier.wait(timeout=60)
                except threading.BrokenBarrierError:
                    raise MeasureError(f"{res.where}: the clients did not start together")
                t0 = time.perf_counter()
                for t in threads:
                    t.join()
                wall = time.perf_counter() - t0
                generated, fatal = 0, None
                for k, (state, r) in enumerate(results):
                    run = {"size": "clients", "kind": kind, "index": rnd, "clients": n, "client": k + 1}
                    if state == "ok":
                        run.update(r)
                        d["runs"].append(run)
                        if kind == "run":
                            require(run, ("prompt_n", "prompt_ms", "prompt_per_second", "predicted_n", "predicted_per_second"), res.where)
                            generated += run["tokens"]["generated"] or 0
                            ttft.append(run["stream"]["ttft_ms"])
                            latency.append(run["stream"]["total_ms"])
                        if run.get("output_check") == "garbage":
                            fatal = fatal or MeasureFailure("garbage", res.where)
                    elif state == "busy":
                        d["runs"].append({**run, "error_status": r})
                        errors += kind == "run"
                    else:
                        fatal = fatal or r
                    requests += kind == "run"
                res.save()
                if fatal is not None:
                    raise fatal
                if kind == "run" and generated:
                    tps.append(generated / max(wall, 1e-9))
                log(f"clients {n} {kind} {rnd}: {sum(1 for s, _ in results if s == 'ok')} of {n} answered in {round(wall * 1000)} ms")
        if not ttft:
            d["validity"]["warnings"].append(f"clients {n}: every request was turned away as busy")
        rows.append({"n": n, "rounds": P["rounds"], "requests": requests, "errors": errors,
                     "total_tps": round(statistics.median(tps), 1) if tps else None,
                     "ttft_p50_ms": percentile(sorted(ttft), 50) if ttft else None, "ttft_p95_ms": percentile(sorted(ttft), 95) if ttft else None,
                     "latency_p50_ms": percentile(sorted(latency), 50) if latency else None,
                     "latency_p95_ms": percentile(sorted(latency), 95) if latency else None})
    d["clients"] = rows
    res.save()


def gprobe_excerpt(code: str, size: int, i: int) -> str:
    start = 2000 + (size * 37 + i * 1777) % (len(code) - 4000)
    return code[start:start + GPROBE_CHARS[size]]


def measure_gprobe(base: str, res: Result, log):
    """Agent-shaped probes at ~150/300/450 tokens (see GPROBE_SYSTEM); per size the first PROBE_SKIP calls warm the
    shared system prefix and stay out of the summary."""
    d = res.doc
    code = (PROMPTS / "xlong.txt").read_text(encoding="utf-8")
    for size in GPROBE_CHARS:
        rows = []
        for i in range(PROBE_CALLS):
            msg = [{"role": "system", "content": GPROBE_SYSTEM}, {"role": "user", "content": gprobe_excerpt(code, size, i)}]
            res.where = f"gprobe{size} {i + 1}"
            r = {"size": f"gprobe{size}", "kind": "warmup" if i < PROBE_SKIP else "run", "index": i + 1,
                 **call(base, msg, 1, {"chat_template_kwargs": {"enable_thinking": False}})}
            d["runs"].append(r)
            res.save()
            if i >= PROBE_SKIP:
                require(r, ("prompt_n", "prompt_ms"), f"gprobe{size} {i + 1}")
            rows.append(r)
        kept = rows[PROBE_SKIP:]
        reused = [r["tokens"]["reused"] for r in kept if r["tokens"]["reused"] is not None]
        read = [r["tokens"]["prompt"] for r in kept if r["tokens"]["prompt"] is not None]
        d["sizes"][f"gprobe{size}"] = {"prompt_ms": summary(kept, "prompt_ms"),
                                       "wall_ms": {"median": statistics.median(r["wall_ms"] for r in kept)},
                                       "reused_median": statistics.median(reused) if reused else None,
                                       "read_median": statistics.median(read) if read else None}
        res.save()
        g = d["sizes"][f"gprobe{size}"]
        log(f"gprobe{size}: prompt_ms median {g['prompt_ms']['median']}, read {g['read_median']}, "
            f"reused {g['reused_median']}")
