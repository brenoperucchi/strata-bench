"""The public result schema, recursive. The same spec prunes what the core writes (unknown keys are dropped) and
gates what may be exported (unknown keys are rejected).

Spec forms: SCALAR (str/int/float/bool/None), HEX (hex digest), a dict {key: spec} (only those keys), [spec] (a list
of spec), Map(key_regex, spec) (a dynamic mapping whose keys match key_regex).
"""
import math
import re

SCALAR, HEX, NUM = "scalar", "hex", "num"          # NUM: a finite number or null (never a string or a boolean): the measured metrics


class Map:
    def __init__(self, key_regex: str, spec):
        self.key, self.spec = re.compile(key_regex, re.ASCII), spec       # ASCII: \w and \d mean what they mean in JavaScript


STANDARD_V1_PROMPTS = {
    "medium.txt": "e4c2b6ad18b235d3f0466f0aeb1256612c533188c75b2c5c6741ea6e5463b1c6",
    "long.txt": "2b54045914a7bd362c93587902dc16af4d589cea3493cd0301a116df8495844a",
    "xlong.txt": "acf504f5230c14fdb6e6ca5d8125a9dabd4176d8f5d09882b5a3aed4eee98b83",
    "short.txt": "2cd69728112a942401591b3d19141361105a8b2e9195ae19c007b593d70e9402",
    "probe_lines.txt": "dfa9547550e81cca80b085f57ceb4614fe0c4fba040ddf96fe8d70bb68dde2db",
}
STANDARD_V2_PROMPTS = {         # added by standard-v2 (the agent block); a v2 result may also carry the v1 files' hashes
    "agent_system.txt": "2a421b20eb39a451d56c527c522ad10e1bf05e4e61cec0d195505ad7e38b5bd1",
    "agent_tools.json": "8da51a73ff458b4192d4efd8ea02af2ae8a348583f1f263edfae7dd37fd67584",
    "agent_turns.json": "d842028f1316dc5102b118cb31cbebe2a784d2405b1bce3d1f2d81d8f8c4d108",
    "agent_doc_1.txt": "a6706fef11f389df044a12aba50bf38094712cdf09e29f6b157bbb54a62f7fbb",
    "agent_doc_2.txt": "63fd5d78ab583752286c511534acbef1dae6f73c21a6aff0a470370e2f2d7fb6",
    "agent_doc_3.txt": "a4e9d5a5ebae46b6d04eeffa3737e2217a538906fa16a07ee39ff732d59c3176",
    "agent_doc_4.txt": "21166adfd6dcaab1958814092627ddef3f552d53552d9f00f3263e427ba48ad4",
    "agent_doc_5.txt": "1c4262f60af888cc4cdbc4e5315dda7e09ed9aa99b68bc753a118a534d99fe6b",
}
AGENT_PROTOCOL = {"turns": 5, "sessions": 3, "warmup": 1, "max_tokens": 256, "cache_prompt": "true", "tool_choice": "none"}
CHAT_PROTOCOL = {"runs": 9, "warmup": 1, "max_tokens": 100}                          # short.txt, streamed, answer capped at 100 tokens
CLIENTS_PROTOCOL = {"counts": "2,4,8", "rounds": 2, "warmup": 1, "max_tokens": 128}    # medium.txt from N clients at once, per N: 1 warm-up round and 2 measured
BLOCK_PROTOCOLS = {"agent": AGENT_PROTOCOL, "chat": CHAT_PROTOCOL, "clients": CLIENTS_PROTOCOL}   # the fixed settings a result must repeat for each block it ran
V2_RUN_KEYS = ("stream", "turn", "clients", "client", "error_status")                    # keys of runs[] that only v2 results may carry
BUSY_STATUS = (429,)             # a server that answers 429 (too many requests) to a client of the clients block: counted in errors, not a failure. A 503 is not busy:
#                                  Strata answers 503 while its engine restarts or after it died, so it is a server failure
AGENT_MIN_CTX = 16384            # the five documents, the answers and the tool text need about 14K tokens: a smaller context skips the block
SKIP_PAIRS = {"agent": "context_too_small", "clients": "no_parallel_slots"}     # a skipped block and its one closed reason
TEXT_SIZES = ("short", "medium", "long", "xlong")
SERVER_FIELDS = ("reasoning_budget_tokens", "sampling", "draft_vocab", "fit_max_tokens", "gpu", "layer_split")
SHARED_KEYS = ("reasoning_effort", "temperature", "top_p", "top_k", "seed", "max_tokens",
               "experimental_speed_projection")

SUMMARY = {"median": NUM, "min": NUM, "max": NUM}
GPU = {"name": SCALAR, "vram_gb": SCALAR, "arch": SCALAR, "pcie": SCALAR, "power_limit_w": SCALAR, "role": SCALAR,
       "display": SCALAR}
TIMINGS = {k: NUM for k in ("cache_n", "prompt_n", "prompt_ms", "prompt_per_token_ms", "prompt_per_second",
                               "predicted_n", "predicted_ms", "predicted_per_token_ms", "predicted_per_second",
                               "draft_n", "draft_n_accepted")}
USAGE = {"prompt_tokens": NUM, "completion_tokens": NUM, "total_tokens": NUM,
         "prompt_tokens_details": {"cached_tokens": NUM},
         "completion_tokens_details": {"reasoning_tokens": NUM}}
SAMPLING = {"temperature": SCALAR, "top_p": SCALAR, "top_k": SCALAR, "min_p": SCALAR, "seed": SCALAR,
            "max_tokens": SCALAR, "repeat_penalty": SCALAR, "presence_penalty": SCALAR}

# keys an --identity file may add (declared by the caller), per block
IDENTITY = {
    "machine": {"machine_id": SCALAR, "os": SCALAR, "wsl": SCALAR, "container": SCALAR, "gpus": [GPU], "cpu": SCALAR,
                "cpu_isa": SCALAR, "cpu_isa_used": SCALAR, "cores": SCALAR, "ram_gb": SCALAR, "ram_type": SCALAR,
                "ram_speed_mts": SCALAR, "driver": SCALAR, "cuda": SCALAR, "hip": SCALAR, "hardware_hash": HEX},
    "engine": {"version": SCALAR, "commit": SCALAR, "binary_sha256": HEX, "source": SCALAR, "build_options": [SCALAR],
               "backend": SCALAR, "server_py_version": SCALAR, "build_info": SCALAR},
    "model": {"family": SCALAR, "quant": SCALAR, "repo": SCALAR, "revision": SCALAR,
              "files_sha256": Map(r"^[\w.-]+$", HEX), "storage": SCALAR, "mtp": SCALAR, "draft_vocab": SCALAR,
              "expert_profile": SCALAR, "hashes_verified_on_server": SCALAR, "file": SCALAR, "n_ctx": SCALAR},
    "calibration": {"ran": SCALAR, "kept_params": Map(r"^--?[\w-]+$", SCALAR), "pool_workers_asked": SCALAR,
                    "pool_workers_resolved": SCALAR, "sweep_summary": SCALAR, "state": SCALAR},
    "config": {"origin_verified": SCALAR},
    "submission": {"by": SCALAR},
}
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,38}")   # submission.by and machine.machine_id: a short name, never free text
OBSERVED = {"engine": {"build_info"}, "model": {"file", "n_ctx"}}

SCHEMA_IDS = {"standard-v1": "strata-bench/v1", "standard-v2": "strata-bench/v2"}   # protocol id -> the schema it is written in
HOST_RELATIONS = ("same-host", "lan", "remote")       # v2: where the client ran relative to the server (declared); first-token time includes the network
SUITES = ("custom", "quick", "standard", "full")      # v2: which profile of blocks was run ("custom" = the sizes picked with --sizes)
BLOCKS = ("first-token", "chat", "agent", "clients")        # decode-curve is reserved and not accepted until a protocol exists for it
SUITE_SIZES = {"quick": ["chat", "medium", "long"], "standard": ["chat", "medium", "long", "xlong", "agent"],
               "full": ["chat", "medium", "long", "xlong", "agent", "clients"]}     # what --suite runs; the probes and gprobe are v1-style extras, asked for with --sizes
SUITE_BLOCKS = {"quick": ["first-token", "chat"], "standard": ["first-token", "chat", "agent"],
                "full": ["first-token", "chat", "agent", "clients"]}                  # the blocks a result of that suite lists, in this order

FAILURE_KINDS = ("crash", "oom", "garbage", "hang", "server_error")        # the health view: only catastrophic failures, a closed list
FAILURE_WHERE = re.compile(r"^((short|medium|long|xlong) (warmup|run) \d{1,3}|(probe|gprobe(150|300|450)) \d{1,3}|agent (warmup|run) \d{1,3} turn [1-5]|chat (warmup|run) \d{1,3}|clients \d{1,3} (warmup|round) \d{1,3})$", re.ASCII)

AGENT_TURN_SUMMARY = {k: SUMMARY for k in ("prompt_n", "cache_n", "predicted_n", "ttft_ms", "prompt_ms", "decode_tps")}
AGENT = {"sessions": [{"index": NUM, "hit_pct": NUM, "reread_tokens": NUM, "new_tokens": NUM}],
         "hit_pct": SUMMARY, "reread_tokens": SUMMARY, "turns": Map(r"^[1-5]$", AGENT_TURN_SUMMARY)}

CLIENTS_ROW = {k: NUM for k in ("n", "rounds", "requests", "errors", "total_tps", "ttft_p50_ms", "ttft_p95_ms", "latency_p50_ms", "latency_p95_ms")}

STREAM = {"ttft_ms": NUM, "total_ms": NUM, "pieces": NUM, "gap_ms": {"p50": NUM, "p95": NUM, "max": NUM}}

RUN = {"size": SCALAR, "kind": SCALAR, "index": NUM, "turn": NUM, "clients": NUM, "client": NUM, "error_status": NUM, "request_sha256": HEX, "wall_ms": NUM, "timings": TIMINGS,
       "usage": USAGE, "tokens": {"prompt": NUM, "reused": NUM, "generated": NUM},
       "reasoning_chars": NUM, "content_chars": NUM, "finish_reason": SCALAR, "output_sha256": HEX, "output_check": SCALAR,
       "stream": STREAM}

PUBLIC = {
    "schema": SCALAR, "label": SCALAR, "build_info": SCALAR, "model_path": SCALAR, "n_ctx": SCALAR,
    "runner": {"name": SCALAR, "version": SCALAR, "commit": SCALAR, "host_relation": SCALAR},
    "submission": {"label": SCALAR, "date": SCALAR, "by": SCALAR},
    "machine": IDENTITY["machine"],
    "engine": IDENTITY["engine"],
    "model": IDENTITY["model"],
    "config": {"args": [SCALAR], "env": Map(r"^[A-Za-z_][A-Za-z0-9_]*$", SCALAR),
               "server": {"reasoning_budget_tokens": SCALAR, "sampling": SAMPLING, "draft_vocab": SCALAR,
                          "fit_max_tokens": SCALAR, "gpu": SCALAR, "layer_split": [SCALAR]},
               "server_shared": {k: SCALAR for k in SHARED_KEYS},
               "config_id": HEX, "config_origin": SCALAR, "config_origin_verified": SCALAR,
               "calibration": IDENTITY["calibration"]},
    "protocol": {"id": SCALAR, "prompt_sha256": Map(r"^[\w.-]+\.(txt|json)$", HEX), "probe_system_sha256": HEX,
                 "sampling": {"temperature": SCALAR, "seed": SCALAR, "cache_prompt": SCALAR},
                 "max_tokens": SCALAR, "warmup": SCALAR, "runs": SCALAR,
                 "probe": {"calls": SCALAR, "skip_first": SCALAR, "max_tokens": SCALAR, "offset": SCALAR,
                           "cache_prompt": SCALAR},
                 "gprobe": {"system_sha256": HEX, "chars": Map(r"^(150|300|450)$", SCALAR), "source": SCALAR,
                            "calls": SCALAR, "skip_first": SCALAR, "max_tokens": SCALAR},
                 "reasoning_sent": {"sizes": SCALAR, "probe": SCALAR}, "unique_line": SCALAR,
                 "suite": SCALAR, "blocks": [SCALAR], "skipped": [{"block": SCALAR, "reason": SCALAR}],
                 "agent": {k: SCALAR for k in AGENT_PROTOCOL}, "chat": {k: SCALAR for k in CHAT_PROTOCOL},
                 "clients": {k: SCALAR for k in CLIENTS_PROTOCOL}},
    "runs": [RUN],
    "agent": AGENT,
    "clients": [CLIENTS_ROW],
    "sizes": Map(r"^(short|medium|long|xlong|chat|probe|gprobe150|gprobe300|gprobe450)$",
                 {"prompt_tps": SUMMARY, "decode_tps": SUMMARY, "prompt_ms": SUMMARY, "wall_ms": SUMMARY, "ttft_ms": SUMMARY,
                  "reused_median": NUM, "read_median": NUM}),
    "validity": {"status": SCALAR, "warnings": [SCALAR], "error": SCALAR,
                 "failure": {"kind": SCALAR, "where": SCALAR, "http_status": NUM}},
}

_HEX = re.compile(r"[0-9a-f]{8,64}")


def is_num(value) -> bool:
    """null or a finite number; a string, a boolean, a list or NaN is not (JSON.parse in JavaScript rejects the NaN and Infinity literals too)."""
    if value is None:
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def check(value, spec, where="", problems=None) -> list:
    """Every key and value that does not fit spec, as 'where: why'."""
    problems = [] if problems is None else problems
    if spec == SCALAR:
        if not (value is None or isinstance(value, (str, int, float, bool))):
            problems.append(f"{where}: not a scalar")
    elif spec == NUM:
        if not is_num(value):
            problems.append(f"{where}: not a number")
    elif spec == HEX:
        if not (isinstance(value, str) and _HEX.fullmatch(value)):
            problems.append(f"{where}: not a hex digest")
    elif isinstance(spec, list):
        if not isinstance(value, list):
            problems.append(f"{where}: not a list")
        else:
            for i, v in enumerate(value):
                check(v, spec[0], f"{where}[{i}]", problems)
    elif isinstance(spec, Map):
        if not isinstance(value, dict):
            problems.append(f"{where}: not an object")
        else:
            for k, v in value.items():
                if not spec.key.fullmatch(str(k)):
                    problems.append(f"{where}.{k}: key not allowed")
                else:
                    check(v, spec.spec, f"{where}.{k}", problems)
    elif isinstance(spec, dict):
        if not isinstance(value, dict):
            problems.append(f"{where}: not an object")
        else:
            for k, v in value.items():
                if k not in spec:
                    problems.append(f"{where}.{k}: not in the public schema".lstrip("."))
                else:
                    check(v, spec[k], f"{where}.{k}".lstrip("."), problems)
    return problems


def prune(value, spec):
    """value with everything outside spec removed (None when value itself does not fit)."""
    if spec == SCALAR:
        return value if value is None or isinstance(value, (str, int, float, bool)) else None
    if spec == NUM:
        return value if is_num(value) else None
    if spec == HEX:
        return value if isinstance(value, str) and _HEX.fullmatch(value) else None
    if isinstance(spec, list):
        return [prune(v, spec[0]) for v in value] if isinstance(value, list) else None
    if isinstance(spec, Map):
        return ({k: prune(v, spec.spec) for k, v in value.items() if spec.key.fullmatch(str(k))}
                if isinstance(value, dict) else None)
    if isinstance(spec, dict):
        return {k: prune(v, spec[k]) for k, v in value.items() if k in spec} if isinstance(value, dict) else None
    return None
