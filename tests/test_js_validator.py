"""docs/validator.js (browser + upload worker) must return EXACTLY the problems strata_bench.validate returns, on real results and on every kind of tampering."""
import copy
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from strata_bench import core, validate
from tests import test_core as TC

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
IDENT = {"machine": {"gpus": [{"name": "RTX 5090", "vram_gb": 32}], "cpu": "Ryzen 9 5950X", "ram_gb": 96, "os": "Windows 11"},
         "engine": {"version": "0.1.40.2", "commit": "abc1234"}, "model": {"family": "Swift", "quant": "IQ3_XXS"}}


def real(tmp, label, mode=None, sizes=("medium", "probe", "gprobe"), run_kw=None, **kw):
    TC.Fake.reset()
    TC.Fake.st["mode"], TC.Fake.st["mode_after"] = mode, 1
    TC.Fake.st.update(kw)
    srv = TC.Threaded(("127.0.0.1", 0), TC.Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        core.run(f"http://127.0.0.1:{srv.server_port}", TC.CFG, "setup-default", list(sizes), label, tmp, IDENT, log=lambda *a: None, **(run_kw or {}))
    finally:
        srv.shutdown()
        srv.server_close()
    return json.loads((tmp / f"{label}.result.json").read_text())


@unittest.skipUnless(NODE, "node is not installed")
class JsParity(unittest.TestCase):
    def cases(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        ok = real(tmp, "ok")
        failed = real(tmp, "failed", mode="crash")
        warn = real(tmp, "warn", sizes=("medium",), reused=500)
        TC.Fake.reset()
        inc = real(tmp, "inc", mode="bad_request")
        v2 = real(tmp, "v2", sizes=("medium", "probe"), run_kw={"protocol": core.PROTOCOL_V2})
        v2_failed = real(tmp, "v2failed", mode="crash", sizes=("medium",), run_kw={"protocol": core.PROTOCOL_V2})
        agent = real(tmp, "agent", sizes=("agent",), run_kw={"protocol": core.PROTOCOL_V2})
        agent_failed = real(tmp, "agentfail", mode="oom", sizes=("agent",), run_kw={"protocol": core.PROTOCOL_V2})
        chat = real(tmp, "chat", sizes=("chat",), run_kw={"protocol": core.PROTOCOL_V2})
        clients = real(tmp, "clients", sizes=("clients",), run_kw={"protocol": core.PROTOCOL_V2})
        busy = real(tmp, "busy", sizes=("clients",), run_kw={"protocol": core.PROTOCOL_V2}, busy_client="2", busy_status=429)
        clients_failed = real(tmp, "clientsfail", mode="oom", sizes=("clients",), run_kw={"protocol": core.PROTOCOL_V2})
        quick = real(tmp, "quick", sizes=("medium",), run_kw={"protocol": core.PROTOCOL_V2, "suite": "quick"})
        full = real(tmp, "full", sizes=("medium",), run_kw={"protocol": core.PROTOCOL_V2, "suite": "full"})
        out = {"ok": ok, "failed": failed, "warnings": warn, "v2 ok": v2, "v2 failed": v2_failed, "v2 agent ok": agent,
               "v2 agent failed": agent_failed, "v2 chat ok": chat, "v2 clients ok": clients, "v2 clients busy": busy,
               "v2 clients failed": clients_failed, "v2 quick": quick, "v2 full": full, "incomplete": inc, "empty": {}}

        def mut(name, fn, base=ok):
            d = copy.deepcopy(base)
            fn(d)
            out[name] = d
        mut("by set", lambda d: d["submission"].update(by="breno-p"))
        mut("by with space", lambda d: d["submission"].update(by="two words"))
        mut("by a number", lambda d: d["submission"].update(by=5))
        mut("by a list", lambda d: d["submission"].update(by=["x"]))
        mut("by too long", lambda d: d["submission"].update(by="a" * 40))
        mut("by 39 chars", lambda d: d["submission"].update(by="a" * 39))
        mut("by an email", lambda d: d["submission"].update(by="me@example.com"))
        mut("machine id set", lambda d: d["machine"].update(machine_id="rig-1"))
        mut("machine id bad", lambda d: d["machine"].update(machine_id="my rig"))
        mut("unknown top key", lambda d: d.update(hostname="my-box"))
        mut("unknown nested key", lambda d: d["machine"].update(serial="123"))
        mut("scalar where object", lambda d: d.update(machine="x"))
        mut("object where scalar", lambda d: d.update(label={"a": 1}))
        mut("list not list", lambda d: d.update(runs={"a": 1}))
        mut("bad hex", lambda d: d["runs"][0].update(request_sha256="not-hex"))
        mut("bad hex upper", lambda d: d["runs"][0].update(output_sha256="ABCDEF12"))
        mut("size key not allowed", lambda d: d["sizes"].update(giant={"prompt_tps": {"median": 1}}))
        mut("windows path", lambda d: d["config"].update(args=["--pack", "E:\\private\\pack"]))
        mut("absolute path", lambda d: d.update(label="/home/someone/run"))
        mut("private ip 192", lambda d: d.update(label="host 192.168.1.5"))
        mut("private ip 10", lambda d: d.update(label="host 10.0.0.1"))
        mut("private ip 172", lambda d: d.update(label="host 172.16.4.2"))
        mut("loopback", lambda d: d.update(label="127.0.0.1"))
        mut("token sk", lambda d: d["config"]["env"].update(X="sk-abcdefgh12345"))
        mut("token gh", lambda d: d.update(label="ghp_abcdefgh12345"))
        mut("network share", lambda d: d.update(label="\\\\server\\share\\x"))
        mut("two leaks", lambda d: d.update(label="E:\\a 10.1.2.3"))
        mut("schema id", lambda d: d.update(schema="strata-bench/v2"))
        mut("schema id missing", lambda d: d.pop("schema"))
        mut("protocol id", lambda d: d["protocol"].update(id="custom"))
        mut("protocol missing", lambda d: d.pop("protocol"))
        mut("prompt hash", lambda d: d["protocol"]["prompt_sha256"].update({"medium.txt": "0" * 64}))
        mut("prompt unknown", lambda d: d["protocol"]["prompt_sha256"].update({"extra.txt": "0" * 64}))
        mut("probe system", lambda d: d["protocol"].update(probe_system_sha256="0" * 64))
        mut("gprobe system", lambda d: d["protocol"]["gprobe"].update(system_sha256="0" * 64))
        mut("gprobe chars", lambda d: d["protocol"]["gprobe"]["chars"].update({"150": 1}))
        for st in ("running", "incomplete", None, 5, True, "OK"):
            mut(f"status {st!r}", lambda d, st=st: d["validity"].update(status=st))
        mut("status removed", lambda d: d["validity"].pop("status"))
        mut("failure bad kind", lambda d: d["validity"]["failure"].update(kind="meltdown"), failed)
        mut("failure kind missing", lambda d: d["validity"]["failure"].pop("kind"), failed)
        mut("failure removed", lambda d: d["validity"].pop("failure"), failed)
        mut("failure where path", lambda d: d["validity"]["failure"].update(where="E:\\x"), failed)
        mut("failure where free text", lambda d: d["validity"]["failure"].update(where="medium run 1; rm"), failed)
        mut("failure where missing", lambda d: d["validity"]["failure"].pop("where"), failed)
        mut("failure on ok", lambda d: d["validity"].update(failure={"kind": "crash", "where": "medium run 1"}))
        mut("failure not object", lambda d: d["validity"].update(failure="crash"), failed)
        mut("failure extra key", lambda d: d["validity"]["failure"].update(note="x"), failed)
        mut("errors with leak", lambda d: d["validity"].update(error="boom at E:\\x\\y.cu"), failed)
        # standard-v2 (additive): the new fields are closed, paired with the v2 schema, and absent from v1
        mut("v2 schema on v1 protocol", lambda d: d.update(schema="strata-bench/v2"), ok)
        mut("v1 schema on v2 protocol", lambda d: d.update(schema="strata-bench/v1"), v2)
        mut("schema v3", lambda d: d.update(schema="strata-bench/v3"), v2)
        mut("protocol v3", lambda d: d["protocol"].update(id="standard-v3"), v2)
        mut("protocol id a list", lambda d: d["protocol"].update(id=["standard-v2"]), v2)
        mut("protocol id an object", lambda d: d["protocol"].update(id={"a": 1}), v2)
        mut("host relation cloud", lambda d: d["runner"].update(host_relation="cloud"), v2)
        mut("host relation lan", lambda d: d["runner"].update(host_relation="lan"), v2)
        mut("host relation missing", lambda d: d["runner"].pop("host_relation"), v2)
        mut("host relation a list", lambda d: d["runner"].update(host_relation=["lan"]), v2)
        mut("suite bad", lambda d: d["protocol"].update(suite="everything"), v2)
        mut("suite missing", lambda d: d["protocol"].pop("suite"), v2)
        mut("blocks empty", lambda d: d["protocol"].update(blocks=[]), v2)
        mut("blocks bad", lambda d: d["protocol"].update(blocks=["first-token", "free-text"]), v2)
        mut("blocks a string", lambda d: d["protocol"].update(blocks="first-token"), v2)
        mut("blocks nested", lambda d: d["protocol"].update(blocks=[["first-token"]]), v2)
        mut("blocks several", lambda d: d["protocol"].update(blocks=["first-token", "decode-curve"]), v2)
        mut("stream extra key", lambda d: d["runs"][0]["stream"].update(note="x"), v2)
        mut("stream gap extra key", lambda d: d["runs"][0]["stream"].update(gap_ms={"p50": 1, "p99": 2}), v2)
        mut("stream ttft a list", lambda d: d["runs"][0]["stream"].update(ttft_ms=[1]), v2)
        mut("size ttft extra key", lambda d: d["sizes"]["medium"].update(ttft_ms={"mean": 1}), v2)
        mut("stream with a leak", lambda d: d["runs"][0]["stream"].update(ttft_ms="E:\\x"), v2)
        mut("v1 with host relation", lambda d: d["runner"].update(host_relation="lan"))
        mut("v1 with suite", lambda d: d["protocol"].update(suite="custom"))
        mut("v1 with blocks", lambda d: d["protocol"].update(blocks=["first-token"]))
        mut("v1 with stream", lambda d: d["runs"][0].update(stream={"ttft_ms": 1}))
        mut("v1 with size ttft", lambda d: d["sizes"]["medium"].update(ttft_ms={"median": 1}))
        def skip_agent(d):
            d.pop("agent")
            d["protocol"].pop("agent")
            d["protocol"]["skipped"] = [{"block": "agent", "reason": "context_too_small"}]
            d["n_ctx"] = 8192
            d["runs"] = []
        mut("agent skipped", skip_agent, agent)
        mut("agent skipped with data", lambda d: (skip_agent(d), d.update(agent={"sessions": [{"index": 1}]}))[0], agent)
        mut("agent data missing", lambda d: d.pop("agent"), agent)
        mut("agent sessions empty", lambda d: d["agent"].update(sessions=[]), agent)
        mut("agent sessions a string", lambda d: d["agent"].update(sessions="x"), agent)
        mut("agent not in blocks", lambda d: d["protocol"].update(blocks=["first-token"]), agent)
        mut("agent run in a first-token result", lambda d: d["runs"][0].update(size="agent"), v2)
        mut("agent data in a first-token result", lambda d: d.update(agent=copy.deepcopy(agent["agent"])), v2)
        mut("protocol agent turns", lambda d: d["protocol"]["agent"].update(turns=4), agent)
        mut("protocol agent warmup true", lambda d: d["protocol"]["agent"].update(warmup=True), agent)
        mut("protocol agent missing", lambda d: d["protocol"].pop("agent"), agent)
        mut("protocol agent extra", lambda d: d["protocol"]["agent"].update(x=1), agent)
        mut("protocol agent a string", lambda d: d["protocol"].update(agent="x"), agent)
        mut("skipped bad reason", lambda d: d["protocol"].update(skipped=[{"block": "agent", "reason": "slow"}]), agent)
        mut("skipped bad block", lambda d: d["protocol"].update(skipped=[{"block": "chat", "reason": "context_too_small"}]), agent)
        mut("skipped not a list", lambda d: d["protocol"].update(skipped="agent"), agent)
        mut("skipped entry a string", lambda d: d["protocol"].update(skipped=["agent"]), agent)
        mut("skipped free text", lambda d: d["protocol"].update(skipped=[{"block": "agent", "reason": "context_too_small", "note": "x"}]), agent)
        mut("agent extra key", lambda d: d["agent"].update(note="x"), agent)
        mut("agent session extra key", lambda d: d["agent"]["sessions"][0].update(text="x"), agent)
        mut("agent turn six", lambda d: d["agent"]["turns"].update({"6": {}}), agent)
        mut("agent hit pct a list", lambda d: d["agent"]["sessions"][0].update(hit_pct=[1]), agent)
        mut("agent run turn extra", lambda d: d["runs"][0].update(turn=[1]), agent)
        mut("agent prompt hash", lambda d: d["protocol"]["prompt_sha256"].update({"agent_doc_1.txt": "0" * 64}), agent)
        mut("agent prompt unknown", lambda d: d["protocol"]["prompt_sha256"].update({"agent_doc_9.txt": "0" * 64}), agent)
        mut("agent prompt in v1", lambda d: (d.update(schema="strata-bench/v1"), d["protocol"].update(id="standard-v1")), agent)
        mut("v1 with agent", lambda d: d.update(agent={"sessions": [{"index": 1}]}))
        mut("v1 with skipped", lambda d: d["protocol"].update(skipped=[]))
        mut("v1 with protocol agent", lambda d: d["protocol"].update(agent={"turns": 5}))
        mut("v1 with turn", lambda d: d["runs"][0].update(turn=1))
        for where in ("agent run 2 turn 3", "agent warmup 1 turn 5", "agent run 2 turn 6", "agent run 2", "agent run x turn 1", "agent run 1 turn 1; rm"):
            mut(f"where {where}", lambda d, w=where: d["validity"].update(status="failed", failure={"kind": "crash", "where": w}), agent)
        def skip_clients(d):
            d.pop("clients")
            d["protocol"].pop("clients")
            d["protocol"]["skipped"] = [{"block": "clients", "reason": "no_parallel_slots"}]
            d["runs"] = []
        mut("clients skipped", skip_clients, clients)
        mut("clients skipped with data", lambda d: (skip_clients(d), d.update(clients=[{"n": 2}]))[0], clients)
        mut("clients data missing", lambda d: d.pop("clients"), clients)
        mut("clients empty", lambda d: d.update(clients=[]), clients)
        mut("clients not a list", lambda d: d.update(clients={"n": 2}), clients)
        mut("clients row extra key", lambda d: d["clients"][0].update(note="x"), clients)
        mut("clients row a list", lambda d: d["clients"][0].update(errors=[1]), clients)
        mut("clients protocol counts", lambda d: d["protocol"]["clients"].update(counts="2,4"), clients)
        mut("clients protocol rounds true", lambda d: d["protocol"]["clients"].update(rounds=True), clients)
        mut("clients protocol warmup true", lambda d: d["protocol"]["clients"].update(warmup=True), clients)
        mut("clients protocol missing", lambda d: d["protocol"].pop("clients"), clients)
        mut("clients not in blocks", lambda d: d["protocol"].update(blocks=["first-token"]), clients)
        mut("clients skipped bad reason", lambda d: d["protocol"].update(skipped=[{"block": "clients", "reason": "busy"}]), clients)
        mut("clients data in a chat result", lambda d: d.update(clients=copy.deepcopy(clients["clients"])), chat)
        mut("chat data missing", lambda d: d["sizes"].pop("chat"), chat)
        mut("chat data not an object", lambda d: d["sizes"].update(chat=[1]), chat)
        mut("chat protocol tokens", lambda d: d["protocol"]["chat"].update(max_tokens=256), chat)
        mut("chat protocol runs true", lambda d: d["protocol"]["chat"].update(runs=True), chat)
        mut("chat protocol missing", lambda d: d["protocol"].pop("chat"), chat)
        mut("chat not in blocks", lambda d: d["protocol"].update(blocks=["first-token"]), chat)
        mut("chat data in a first-token result", lambda d: d["sizes"].update(chat={"decode_tps": {"median": 1}}), v2)
        mut("chat protocol in a first-token result", lambda d: d["protocol"].update(chat=dict(chat["protocol"]["chat"])), v2)
        mut("client run key a list", lambda d: d["runs"][0].update(client=[1]), clients)
        mut("busy run extra key", lambda d: [r for r in d["runs"] if "error_status" in r][0].update(note="x"), busy)
        mut("v1 with clients", lambda d: d.update(clients=[]))
        mut("v1 with chat size", lambda d: d["sizes"].update(chat={"decode_tps": {"median": 1}}))
        mut("v1 with protocol chat", lambda d: d["protocol"].update(chat={"runs": 9}))
        mut("v1 with protocol clients", lambda d: d["protocol"].update(clients={"n": 1}))
        mut("v1 with run clients", lambda d: d["runs"][0].update(clients=2))
        mut("v1 with run client", lambda d: d["runs"][0].update(client=1))
        mut("v1 with run error status", lambda d: d["runs"][0].update(error_status=503))
        for where in ("clients 4 round 2", "clients 8 warmup 1", "chat run 9", "chat warmup 1", "clients round 2", "clients 4 round", "chat run", "clients 4 turn 2", "clients 1000 round 1"):
            mut(f"where {where}", lambda d, w=where: d["validity"].update(status="failed", failure={"kind": "crash", "where": w}), clients)
        mut("quick with the standard blocks", lambda d: d["protocol"].update(blocks=["first-token", "chat", "agent"]), quick)
        mut("quick with the blocks reordered", lambda d: d["protocol"].update(blocks=["chat", "first-token"]), quick)
        mut("full with the quick blocks", lambda d: d["protocol"].update(blocks=["first-token", "chat"]), full)
        mut("custom with the quick blocks", lambda d: d["protocol"].update(suite="custom"), quick)
        mut("suite a list", lambda d: d["protocol"].update(suite=["quick"]), quick)
        mut("suite an object", lambda d: d["protocol"].update(suite={"quick": 1}), quick)
        mut("suite unknown", lambda d: d["protocol"].update(suite="huge"), quick)
        # numbers are numbers: a string or a boolean in a metric is refused (and cannot reach the dashboard as markup)
        html = "<img src=x onerror=alert(1)>"
        for label, path, base in (("summary median", ("sizes", "medium", "prompt_tps", "median"), ok), ("run timing", ("runs", 0, "timings", "prompt_n"), ok),
                                  ("run tokens", ("runs", 0, "tokens", "prompt"), ok), ("run wall", ("runs", 0, "wall_ms"), ok),
                                  ("run index", ("runs", 0, "index"), ok), ("run usage", ("runs", 0, "usage", "completion_tokens"), ok),
                                  ("stream ttft", ("runs", 0, "stream", "ttft_ms"), v2), ("size ttft", ("sizes", "medium", "ttft_ms", "median"), v2),
                                  ("agent hit", ("agent", "sessions", 0, "hit_pct"), agent), ("agent turn", ("agent", "turns", "1", "ttft_ms", "median"), agent),
                                  ("clients tps", ("clients", 0, "total_tps"), clients), ("clients n", ("clients", 0, "n"), clients),
                                  ("failure status", ("validity", "failure", "http_status"), failed)):
            for kind, value in (("html", html), ("bool", True), ("list", [1]), ("string number", "12")):
                def put(d, path=path, value=value):
                    node = d
                    for key in path[:-1]:
                        node = node[key]
                    node[path[-1]] = value
                mut(f"number {label} {kind}", put, base)
        # an own key only: names every object inherits are not fields
        for key in ("constructor", "toString", "hasOwnProperty", "valueOf", "__proto__"):
            for where, path, base in (("root", (), ok), ("machine", ("machine",), ok), ("runner", ("runner",), ok), ("stream", ("runs", 0, "stream"), v2),
                                      ("agent", ("agent",), agent), ("protocol agent", ("protocol", "agent"), agent), ("client row", ("clients", 0), clients),
                                      ("size", ("sizes", "medium"), ok)):
                def inject(d, path=path, key=key):
                    node = d
                    for step in path:
                        node = node[step]
                    node[key] = {"host": "internal-only.example", "secret": "opaque-secret-123"}
                mut(f"inherited key {key} in {where}", inject, base)
        # containers that are not objects: no exception on either side, the same problems
        for where, path in (("protocol", ("protocol",)), ("prompt hashes", ("protocol", "prompt_sha256")), ("gprobe", ("protocol", "gprobe")), ("validity", ("validity",)),
                            ("runner", ("runner",)), ("sizes", ("sizes",)), ("runs", ("runs",)), ("machine", ("machine",)), ("submission", ("submission",)),
                            ("failure", ("validity", "failure"))):
            for kind, value in (("string", "x"), ("number", 5), ("true", True), ("zero", 0), ("empty string", ""), ("list", [1]), ("empty list", []), ("null", None)):
                def swap(d, path=path, value=value):
                    node = d
                    for step in path[:-1]:
                        node = node[step]
                    node[path[-1]] = value
                mut(f"container {where} as {kind}", swap, failed if where == "failure" else ok)
        for where, path in (("agent", ("agent",)), ("protocol agent", ("protocol", "agent")), ("protocol blocks", ("protocol", "blocks")), ("clients", ("clients",)),
                            ("protocol skipped", ("protocol", "skipped")), ("sizes chat", ("sizes", "chat"))):
            for kind, value in (("string", "x"), ("number", 5), ("true", True), ("list", [1]), ("empty list", []), ("object", {"a": 1})):
                def swap2(d, path=path, value=value):
                    node = d
                    for step in path[:-1]:
                        node = node[step]
                    node[path[-1]] = value
                mut(f"v2 container {where} as {kind}", swap2, full)
        # patterns mean the same thing on both sides: no trailing newline, no non-ASCII digits or word characters
        mut("hash with a newline", lambda d: d["runs"][0].update(request_sha256="abcdef12\n"))
        mut("env key with a newline", lambda d: d["config"]["env"].update({"FOO\n": "1"}))
        mut("where with a newline", lambda d: d["validity"].update(status="failed", failure={"kind": "crash", "where": "medium run 1\n"}))
        mut("where with arabic digits", lambda d: d["validity"].update(status="failed", failure={"kind": "crash", "where": "medium run \u0663"}))
        mut("model file key with accents", lambda d: d["model"].update(files_sha256={"modelo-\u00e7\u00e3o.gguf": "ab" * 32}))
        mut("by with a newline", lambda d: d["submission"].update(by="name\n"))
        mut("leak after a non-ascii letter", lambda d: d.update(label="\u00e9/tmp/"))
        mut("share after a non-ascii letter", lambda d: d.update(label="\\\u00e9\\"))
        mut("ip after a non-ascii letter", lambda d: d.update(label="\u00e9 10.1.2.3 \u00e9"))
        mut("status with a newline", lambda d: d["validity"].update(status="a\nb"))
        mut("protocol id with a tab", lambda d: d["protocol"].update(id="standard\tv1"))
        # the closed skip pairs and the content a block promises
        mut("skip pair wrong", lambda d: d["protocol"].update(skipped=[{"block": "agent", "reason": "no_parallel_slots"}]), agent)
        mut("skip no stream timings", lambda d: d["protocol"].update(skipped=[{"block": "clients", "reason": "no_stream_timings"}]), clients)
        mut("decode-curve block", lambda d: d["protocol"].update(blocks=d["protocol"]["blocks"] + ["decode-curve"]), v2)
        mut("first-token without content", lambda d: d.update(runs=[], sizes={}), v2)
        mut("first-token without ttft", lambda d: [z.pop("ttft_ms", None) for z in d["sizes"].values()], v2)
        mut("first-token without streams", lambda d: [r.pop("stream", None) for r in d["runs"]], v2)
        mut("chat without ttft", lambda d: d["sizes"]["chat"].pop("ttft_ms"), chat)
        mut("chat missing a run", lambda d: d["runs"].pop(), chat)
        mut("agent missing a session", lambda d: d["agent"]["sessions"].pop(), agent)
        mut("agent missing a run", lambda d: d["runs"].pop(), agent)
        mut("clients missing a row", lambda d: d["clients"].pop(), clients)
        mut("clients wrong n", lambda d: d["clients"][0].update(n=3), clients)
        mut("clients wrong requests", lambda d: d["clients"][1].update(requests=9), clients)
        mut("clients reordered", lambda d: d["clients"].reverse(), clients)
        mut("full emptied", lambda d: d.update(runs=[], sizes={"chat": {}}, agent={"sessions": [{}, {}, {}]}, clients=[{}]), full)
        # the closed values of a run, and no text in them
        for key, value in (("finish_reason", "internal-host.example:opaque-secret"), ("finish_reason", "<b>x</b>"), ("finish_reason", True), ("finish_reason", 5), ("finish_reason", {"a": 1}),
                           ("output_check", "<b>x</b>"), ("output_check", 1), ("kind", "x"), ("kind", None), ("size", "x"), ("size", None), ("size", ["medium"])):
            mut(f"run {key} {value!r}", lambda d, key=key, value=value: d["runs"][0].update({key: value}))
        for value in ("length", "tool_calls", "other", None):
            mut(f"run finish_reason {value!r} is fine", lambda d, value=value: d["runs"][0].update(finish_reason=value))
        # a lone surrogate (it cannot be UTF-8; JavaScript would escape it where Python would not), and a proper pair
        for name, text in (("path", "\ud800C:\\x"), ("ip", "\ud80010.1.2.3"), ("token", "\ud800sk-abcdefgh1234"), ("low", "\udc00 /usr/lib/x/"), ("plain", "\ud800")):
            mut(f"lone surrogate {name}", lambda d, text=text: d.update(label=text))
        mut("lone surrogate in a key", lambda d: d["config"]["env"].update({"\ud800": "1"}))
        mut("surrogate pair is fine", lambda d: d.update(label="ok \U0001F600"))
        # messages never echo an exotic value: the same text whatever the value
        for value in (1.0, -0.0, 1e22, "\u0085", "a\u2028b", 12345678901234567890, "x" * 60):
            mut(f"status {value!r} message", lambda d, value=value: d["validity"].update(status=value))
            mut(f"protocol id {value!r} message", lambda d, value=value: d["protocol"].update(id=value))
        mut("extra keys that look like numbers", lambda d: d.update({"2": "x", "1": "y", "10": "z", "b": 1, "a": 1}))
        # a block that names itself but is a shell: the runs must be the requests the block promises
        mut("clients without its runs", lambda d: d.update(runs=[r for r in d["runs"] if r["size"] != "clients"]), clients)
        mut("agent runs all the same request", lambda d: [r.update(index=1, turn=1) for r in d["runs"] if r["size"] == "agent"], agent)
        mut("agent sessions empty objects", lambda d: d["agent"].update(sessions=[{}, {}, {}]), agent)
        mut("agent sessions without an index", lambda d: [x.pop("index") for x in d["agent"]["sessions"]], agent)
        mut("agent sessions same index", lambda d: [x.update(index=1) for x in d["agent"]["sessions"]], agent)
        mut("agent run turn out of range", lambda d: [r for r in d["runs"] if r["size"] == "agent"][-1].update(turn=6), agent)
        mut("agent one run replaced by another", lambda d: d["runs"].__setitem__(d["runs"].index([r for r in d["runs"] if r["size"] == "agent" and r["kind"] == "run"][0]), copy.deepcopy([r for r in d["runs"] if r["size"] == "agent" and r["kind"] == "run"][1])), agent)
        mut("agent run without a stream", lambda d: [r for r in d["runs"] if r["size"] == "agent" and r["kind"] == "run"][0].pop("stream"), agent)
        mut("agent run with a null ttft", lambda d: [r for r in d["runs"] if r["size"] == "agent" and r["kind"] == "run"][0]["stream"].update(ttft_ms=None), agent)
        mut("all ttft null", lambda d: [r["stream"].update(ttft_ms=None) for r in d["runs"] if "stream" in r], v2)
        mut("size ttft summary empty", lambda d: [z.update(ttft_ms={}) for z in d["sizes"].values() if "ttft_ms" in z], v2)
        mut("size ttft summary without runs", lambda d: d.update(runs=[r for r in d["runs"] if r["size"] != "medium"]), v2)
        mut("chat duplicate run", lambda d: [r for r in d["runs"] if r["size"] == "chat" and r["kind"] == "run"][1].update(index=1), chat)
        mut("chat run index out of range", lambda d: [r for r in d["runs"] if r["size"] == "chat" and r["kind"] == "run"][0].update(index=10), chat)
        mut("chat median null", lambda d: d["sizes"]["chat"]["ttft_ms"].update(median=None), chat)
        mut("clients duplicate request", lambda d: [r for r in d["runs"] if r["size"] == "clients" and r["kind"] == "run"][1].update(client=1, index=1, clients=2), clients)
        mut("clients errors do not match", lambda d: d["clients"][0].update(errors=1), clients)
        mut("clients row missing a key", lambda d: d["clients"][0].pop("total_tps"), clients)
        mut("clients refusal with a 503", lambda d: [r for r in d["runs"] if "error_status" in r and r["kind"] == "run"][0].update(error_status=503), busy)
        mut("clients refusal that has a stream", lambda d: [r for r in d["runs"] if "error_status" in r and r["kind"] == "run"][0].update(stream={"ttft_ms": 1}), busy)
        mut("clients warm-up refusal with a 503", lambda d: [r for r in d["runs"] if "error_status" in r and r["kind"] == "warmup"][0].update(error_status=503), busy)
        mut("clients warm-up refusal that has a stream", lambda d: [r for r in d["runs"] if "error_status" in r and r["kind"] == "warmup"][0].update(stream={"ttft_ms": 1}), busy)
        mut("clients extra run", lambda d: d["runs"].append(copy.deepcopy([r for r in d["runs"] if r["size"] == "clients" and r["kind"] == "run"][0])), clients)
        mut("agent skip with a big context", lambda d: (skip_agent(d), d.update(n_ctx=32768))[0], agent)
        mut("agent skip with a text context", lambda d: (skip_agent(d), d.update(n_ctx="8192"))[0], agent)
        mut("agent skip with a null context", lambda d: (skip_agent(d), d.update(n_ctx=None))[0], agent)
        mut("agent skip at the limit", lambda d: (skip_agent(d), d.update(n_ctx=16384))[0], agent)
        mut("agent skip just under the limit", lambda d: (skip_agent(d), d.update(n_ctx=16383))[0], agent)
        # the scout's findings: a leak is judged on the decoded text, a credential flag keeps no value, numbers stay in range, health agrees with itself, nesting is bounded
        for name, text in (("newline then token", "\nsk-abcdefgh12345"), ("newline then path", "\n/home/alice/secret"), ("newline then ip", "\n10.1.2.3"), ("nul then token", "\x00sk-abcdefgh12345"),
                           ("tab then token", "\tsk-abcdefgh12345"), ("letters then token", "xsk-abcdefgh12345 sk-abcdefgh12345"), ("unc share", "\\\\server\\share\\x"), ("windows path", "see E:\\private\\x")):
            mut(f"leak {name}", lambda d, text=text: d.update(label=text))
        mut("leak in a key", lambda d: d["config"]["env"].update({"/home/alice/x/": "1"}))
        mut("leak in a list", lambda d: d["config"].update(args=["--a", "\nsk-abcdefgh12345"]))
        mut("leaks in two keys, numeric names", lambda d: d.update({"2": "/alpha/secret/", "1": "/beta/secret/"}))
        mut("two leak kinds", lambda d: d.update(label="\n10.1.2.3 \nsk-abcdefgh12345 /home/alice/x/"))
        for flag in (["--api-key", "opaque-demo-secret"], ["--host", "gpu-box.internal"], ["--token=abc"], ["--endpoint=http://example.com"], ["--hf-token", "x"], ["--bind", "0.0.0.0"]):
            mut(f"args carry {flag[0]}", lambda d, flag=flag: d["config"]["args"].extend(flag))
        for flag in (["--api-key", "<redacted>"], ["--host=<host>"], ["--token=<redacted>"], ["--api-key"], ["--api-key", "--max-context"], ["--proxy", "<host>", "--kv", "int8"]):
            mut(f"args placeholder {flag[0]} {len(flag)} is fine", lambda d, flag=flag: d["config"]["args"].extend(flag))
        for name, value in (("integer too big", 10 ** 400), ("small", 12), ("negative huge", -10 ** 400)):
            for where, setter in (("n_ctx", lambda d, v: d.update(n_ctx=v)), ("status", lambda d, v: d["validity"].update(status=v)), ("protocol id", lambda d, v: d["protocol"].update(id=v)),
                                  ("run index", lambda d, v: d["runs"][0].update(index=v)), ("machine ram", lambda d, v: d["machine"].update(ram_gb=v)), ("agent index", None)):
                if setter is not None:
                    mut(f"number {name} in {where}", lambda d, setter=setter, value=value: setter(d, value))
        mut("agent index too big", lambda d: d["agent"]["sessions"][0].update(index=10 ** 400), agent)
        mut("agent run turn too big", lambda d: [r for r in d["runs"] if r["size"] == "agent" and r["kind"] == "run"][0].update(turn=10 ** 400), agent)
        mut("clients client too big", lambda d: [r for r in d["runs"] if r["size"] == "clients" and r["kind"] == "run"][0].update(client=10 ** 400), clients)
        mut("agent index beyond exact", lambda d: d["agent"]["sessions"][0].update(index=2 ** 60), agent)
        # a block that names itself must carry what a finished request carries
        mut("agent minimal shell", lambda d: (d.update(agent={"sessions": [{"index": i, "hit_pct": None, "reread_tokens": None, "new_tokens": None} for i in (1, 2, 3)]}),
                                              [r.clear() or r.update(size="agent", kind="run", index=i, turn=t, stream={"ttft_ms": 1}) for r in [] for i in () for t in ()]), agent)
        def shell(d):
            for r in d["runs"]:
                keep = {k: r[k] for k in ("size", "kind", "index", "turn", "clients", "client") if k in r}
                r.clear()
                r.update(keep, stream={"ttft_ms": 1})
            for z in d["sizes"].values():
                for k in list(z):
                    z[k] = {"median": 1} if k == "ttft_ms" else z[k]
        mut("v2 requests with only a first-token time", shell, v2)
        mut("full requests with only a first-token time", lambda d: (shell(d), d.update(agent={"sessions": [{"index": i, "hit_pct": None, "reread_tokens": None, "new_tokens": None} for i in (1, 2, 3)]})), full)
        mut("run without wall time", lambda d: [r for r in d["runs"] if r["size"] == "medium" and r["kind"] == "run"][0].pop("wall_ms"), v2)
        mut("run without timings", lambda d: [r for r in d["runs"] if r["size"] == "medium" and r["kind"] == "run"][0].update(timings={}), v2)
        mut("run without tokens", lambda d: [r for r in d["runs"] if r["size"] == "medium" and r["kind"] == "run"][0].pop("tokens"), v2)
        mut("run without a request hash", lambda d: [r for r in d["runs"] if r["size"] == "medium" and r["kind"] == "run"][0].pop("request_sha256"), v2)
        mut("size without prompt speed", lambda d: d["sizes"]["medium"].pop("prompt_tps"), v2)
        mut("agent turns missing", lambda d: d["agent"]["turns"].pop("3"), agent)
        mut("agent hit summary missing", lambda d: d["agent"].pop("hit_pct"), agent)
        mut("agent session hit null", lambda d: d["agent"]["sessions"][0].update(hit_pct=None), agent)
        mut("clients rounds null", lambda d: d["clients"][0].update(rounds=None), clients)
        mut("clients rounds 999", lambda d: d["clients"][0].update(rounds=999), clients)
        mut("clients metric null with answers", lambda d: d["clients"][0].update(total_tps=None), clients)
        mut("clients metric with no answers", lambda d: d["clients"][0].update(errors=d["clients"][0]["requests"]), clients)
        # health agrees with itself
        mut("garbage run with status ok", lambda d: [r for r in d["runs"] if r["size"] == "medium" and r["kind"] == "run"][0].update(output_check="garbage"), v2)
        mut("garbage run with a failed result", lambda d: (d["validity"].update(status="failed", failure={"kind": "garbage", "where": "medium run 1"}), [r for r in d["runs"] if r["size"] == "medium" and r["kind"] == "run"][0].update(output_check="garbage")), v2)
        mut("garbage run with a crash failure", lambda d: (d["validity"].update(status="failed", failure={"kind": "crash", "where": "medium run 1"}), [r for r in d["runs"] if r["size"] == "medium" and r["kind"] == "run"][0].update(output_check="garbage")), v2)
        mut("refusal with status ok", lambda d: d["validity"].update(status="ok", warnings=[]), busy)
        mut("ok with a warning", lambda d: d["validity"].update(warnings=["x"]), v2)
        mut("warnings without a warning", lambda d: d["validity"].update(status="warnings", warnings=[]), v2)
        mut("warnings with a warning", lambda d: d["validity"].update(status="warnings", warnings=["x"]), v2)
        mut("ok with an error text", lambda d: d["validity"].update(error="the server reported that it ran out of memory"), v2)
        # nesting is bounded without recursion
        for depth in (30, 200, 700):
            def deep(d, depth=depth):
                node = leaf = {}
                for _ in range(depth):
                    leaf["x"] = {}
                    leaf = leaf["x"]
                d["unexpected"] = node
            mut(f"nested {depth} deep", deep)
            mut(f"nested {depth} deep inside a run", lambda d, deep=deep: deep(d["runs"][0]))
        # declared text is limited: a private hostname is a leak, and no string is longer than the cap
        for name, text in (("hostname internal", "gpu-box.internal"), ("hostname upper", "GPU-BOX.INTERNAL"), ("hostname in a sentence", "run on gpu-box.lan today"), ("hostname corp", "a.b.corp"),
                           ("hostname local", "rig.local"), ("hostname with a dot after", "rig.home.")):
            mut(f"leak {name}", lambda d, text=text: d.update(label=text))
        for name, text in (("not a hostname localhost", "my.localhost"), ("not a hostname homepage", "x.homepage.com"), ("a version", "Strata 0.1.43.1"), ("200 chars", "a" * 200), ("150 emoji", "\U0001F600" * 150)):
            mut(f"label {name} is fine", lambda d, text=text: d.update(label=text))
        for name, text in (("201 chars", "a" * 201), ("201 emoji", "\U0001F600" * 201), ("1000 chars", "b" * 1000)):
            mut(f"label {name}", lambda d, text=text: d.update(label=text))
        mut("key of 201 chars", lambda d: d["config"]["env"].update({"K" * 201: "1"}))
        mut("long string in a list", lambda d: d["config"].update(args=["--a", "x" * 300]))
        mut("long string and a leak", lambda d: d.update(label="y" * 300 + " 10.1.2.3"))
        mut("v2 prompt hash", lambda d: d["protocol"]["prompt_sha256"].update({"medium.txt": "0" * 64}), v2)
        mut("unicode label", lambda d: d.update(label="résultat ✓ 测试"))
        mut("quote in label with leak", lambda d: d.update(label="it's /usr/local/x"))
        return [{"name": k, "doc": v} for k, v in out.items()]

    def test_javascript_returns_exactly_the_python_problems_on_every_case(self):
        cases = self.cases()
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "cases.json").write_text(json.dumps(cases))
        r = subprocess.run([NODE, str(ROOT / "tests" / "js" / "run_cases.mjs"), str(tmp / "cases.json"), str(ROOT / "docs" / "schema.json")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        js = json.loads(r.stdout)
        for c, got in zip(cases, js):
            self.assertEqual(got, validate.problems(c["doc"]), c["name"])
        self.assertGreater(sum(1 for c in cases if validate.problems(c["doc"])), 30)        # the tampering cases really are rejected
        self.assertEqual([c["name"] for c in cases if not validate.problems(c["doc"])], ["ok", "failed", "warnings", "v2 ok", "v2 failed", "v2 agent ok", "v2 agent failed", "v2 chat ok", "v2 clients ok", "v2 clients busy", "v2 clients failed", "v2 quick", "v2 full", "by set", "by 39 chars", "machine id set", "host relation lan", "agent skipped", "where agent run 2 turn 3", "where agent warmup 1 turn 5", "clients skipped", "where clients 4 round 2", "where clients 8 warmup 1", "where chat run 9", "where chat warmup 1", "custom with the quick blocks", "container runs as empty list", "v2 container protocol skipped as empty list", "share after a non-ascii letter", "run finish_reason 'length' is fine", "run finish_reason 'tool_calls' is fine", "run finish_reason 'other' is fine", "run finish_reason None is fine", "surrogate pair is fine", "agent skip just under the limit", "args placeholder --api-key 2 is fine", "args placeholder --host=<host> 1 is fine", "args placeholder --token=<redacted> 1 is fine", "args placeholder --api-key 1 is fine", "args placeholder --proxy 4 is fine", "number small in n_ctx", "number small in run index", "number small in machine ram", "garbage run with a failed result", "garbage run with a crash failure", "warnings with a warning", "label not a hostname localhost is fine", "label not a hostname homepage is fine", "label a version is fine", "label 200 chars is fine", "label 150 emoji is fine", "unicode label"])     # and only genuine results (a non-ASCII label is fine) pass

    def test_a_seeded_fuzz_of_mutations_gets_the_same_problems_from_both_validators(self):
        """Random edits (a value replaced, a key removed, an own or inherited key added) of a v1 result and of v2 results; whatever the edit, both
        validators answer with the same list and neither raises."""
        import random
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        bases = [real(tmp, "f1"), real(tmp, "f2", sizes=("medium", "chat", "agent", "clients"), run_kw={"protocol": core.PROTOCOL_V2, "suite": "full"}),
                 real(tmp, "f3", mode="crash", sizes=("clients",), run_kw={"protocol": core.PROTOCOL_V2})]
        pool = [None, True, False, 0, 7, -3, 1.5, 0.25, "x", "", "a\nb", "\u00e9", "\u00e9/tmp/", "/abs/path/x", "C:\\x", "10.1.2.3", "sk-abcdefgh12345", "ab" * 32, "failed", "ok",
                "standard-v2", "strata-bench/v2", "agent", "clients", 1.0, -0.0, 1e22, "\u0085", "a\u2028b", "\ud800", "\ud800C:\\x", "\udc00sk-abcdefgh1234", {"2": "x", "1": "y"}, "other", "length", [], [1], ["agent"], [{"block": "agent", "reason": "context_too_small"}], {}, {"a": 1}, {"sessions": [{}]}]
        keys = ["constructor", "__proto__", "toString", "extra", "stream", "turn", "agent", "clients", "suite", "blocks", "skipped", "host_relation", "chat", "ttft_ms"]
        rng = random.Random(20261012)

        def paths(node, prefix=()):
            yield prefix
            kids = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ()
            for k, v in kids:
                yield from paths(v, prefix + (k,))
        docs = []
        for i in range(450):
            d = copy.deepcopy(rng.choice(bases))
            for _ in range(rng.choice((1, 1, 2, 3))):
                allpaths = [p for p in paths(d) if p]
                where = rng.choice(allpaths)
                parent = d
                for step in where[:-1]:
                    parent = parent[step]
                move = rng.random()
                if move < 0.55:
                    parent[where[-1]] = copy.deepcopy(rng.choice(pool))
                elif move < 0.75 and isinstance(parent, dict):
                    del parent[where[-1]]
                elif isinstance(parent[where[-1]], dict):
                    parent[where[-1]][rng.choice(keys)] = copy.deepcopy(rng.choice(pool))
                else:
                    parent[where[-1]] = copy.deepcopy(rng.choice(pool))
            docs.append({"name": f"fuzz {i}", "doc": d})
        (tmp / "cases.json").write_text(json.dumps(docs))
        r = subprocess.run([NODE, str(ROOT / "tests" / "js" / "run_cases.mjs"), str(tmp / "cases.json"), str(ROOT / "docs" / "schema.json")], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        js = json.loads(r.stdout)
        for c, got in zip(docs, js):
            self.assertEqual(got, validate.problems(c["doc"]), c["name"] + " " + json.dumps(c["doc"])[:300])
        self.assertGreater(sum(1 for c in docs if validate.problems(c["doc"])), 200)        # the edits really break things

    def test_the_schema_data_file_is_current(self):
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        import build_validator_data
        self.assertEqual(build_validator_data.main(["--check"]), 0)

    def test_the_python_validator_survives_a_non_object(self):
        for doc in ([], "x", 5, None):
            self.assertTrue(validate.problems(doc))


if __name__ == "__main__":
    unittest.main()
