import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from strata_bench import core, schema, validate
from strata_bench.__main__ import main

CFG = {"exe": "E:\\strata-0139\\engine\\strata.exe", "args": [
    "--pack", "E:\\strata-data\\packs\\swift", "--native", "E:\\m\\Swift-IQ3_XXS-00001-of-00002.gguf",
    "--expert-cache", "auto", "--prefill", "auto", "--max-context", "32768", "--kv", "int8"],
    "env": {"STRATA_IQ_MT_MIN": "1"}}
MODEL = "E:\\m\\Swift-IQ3_XXS-00001-of-00002.gguf"
H64 = "ab" * 32


class Fake(BaseHTTPRequestHandler):
    """A Strata server.py stand-in: prompt_n is what was read, cache_n what was reused."""
    st = {}

    @classmethod
    def reset(cls):
        cls.st = {"model": MODEL, "n_ctx": 32768, "build": "Strata 0.1.39", "reused": 0, "probe_read": 20,
                  "probe_reused": 400, "fail_after": None, "no_timings": False, "calls": [], "shared": {},
                  "settings": True, "api_key": None, "mode": None, "mode_after": 0,
                  "sse": {}, "last_render": "", "agent_cache": True}

    def log_message(self, *a):
        pass

    def reply(self, d, code=200):
        b = json.dumps(d).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def authed(self):
        k = Fake.st["api_key"]
        return not k or self.headers.get("authorization") == "Bearer " + k

    def do_GET(self):
        s = Fake.st
        if self.path == "/props":
            p = {"model_path": s["model"], "default_generation_settings": {"n_ctx": s["n_ctx"]} if s["n_ctx"] else {}}
            if s["build"]:
                p["build_info"] = s["build"]
            return self.reply(p)
        if not self.authed():
            return self.reply({"error": "unauthorized"}, 401)
        if self.path == "/settings" and s["settings"]:
            return self.reply({"shared": True, "defaults": s["shared"]})
        if self.path == "/v1/models":
            return self.reply({"data": []})
        self.reply({}, 404)

    def do_POST(self):
        s = Fake.st
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        if not self.authed():
            return self.reply({"error": "unauthorized"}, 401)
        s["calls"].append(body)
        if s["fail_after"] is not None and len(s["calls"]) > s["fail_after"]:
            return self.reply({}, 500)
        mode = s["mode"] if len(s["calls"]) > s["mode_after"] else None
        if mode == "crash":                      # the server dies: the connection is closed without any answer
            self.close_connection = True
            return
        if mode == "oom":
            return self.reply({"error": {"message": "CUDA error: out of memory at E:\\\\private\\\\kernel.cu:12"}}, 500)
        if mode == "bad_request":
            return self.reply({"error": "bad request"}, 400)
        if body.get("stream"):
            return self.stream(body, probe=body["messages"][0]["role"] == "system")
        msg = {"choices": [{"message": {"content": "SAFE", "reasoning_content": "hmm"}, "finish_reason": "stop"}],
               "usage": {"completion_tokens": 1, "secret_extra": "x"}}
        if s["no_timings"]:
            return self.reply(msg)
        probe = body["messages"][0]["role"] == "system"
        read, reused = (s["probe_read"], s["probe_reused"]) if probe else (100, s["reused"])
        if mode == "garbage":
            msg["choices"][0]["message"]["content"] = "tok " * 120
        msg["timings"] = {"prompt_n": read, "cache_n": reused, "prompt_ms": 50.0, "prompt_per_second": 2000.0,
                          "predicted_n": 1, "predicted_ms": 5.0, "predicted_per_second": 200.0, "private_case": "X"}
        self.reply(msg)


    def stream(self, body, probe):
        """An SSE answer (standard-v2). st["sse"] shapes it: header_delay (s before the response starts), keepalive, empty_deltas, pieces, gap (s between pieces), first_delay (s
        before the first piece), drop_after (pieces, then the connection closes), error (an error event after one piece),
        garbage, no_timings, no_done."""
        o = Fake.st["sse"]
        time.sleep(o.get("header_delay", 0))                 # a server that is busy reading the prompt answers late
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()

        def send(ev):
            self.wfile.write(("data: " + (ev if isinstance(ev, str) else json.dumps(ev)) + "\n\n").encode())
            self.wfile.flush()
        time.sleep(o.get("first_delay", 0))
        if o.get("keepalive"):
            self.wfile.write(b": keep-alive\n\n")
            self.wfile.flush()
            time.sleep(o.get("keepalive_delay", 0))
        if o.get("empty_deltas"):
            send({"choices": [{"delta": {"role": "assistant", "content": ""}}]})
            send({"choices": [{"delta": {"content": None, "reasoning_content": ""}}]})
        pieces = o.get("pieces", 3)
        for i in range(pieces):
            if i == o.get("drop_after", -1):
                self.close_connection = True
                return
            if i == 1 and o.get("error"):
                send({"error": {"message": o["error"]}})
                return
            if i:
                time.sleep(o.get("gap", 0))
            text = "tok " * 60 if o.get("garbage") else "SAFE " if i < pieces - 1 else "SAFE"
            send({"choices": [{"delta": {"content": text, "reasoning_content": None}}]})
        s = Fake.st
        read, reused = (s["probe_read"], s["probe_reused"]) if probe else (100, s["reused"])
        if body.get("tools"):                                # an agent turn: the cache keeps the longest common prefix of the last conversation
            render = json.dumps(body["messages"])
            total = len(render) // 4
            reused = len(os.path.commonprefix([s["last_render"], render])) // 4 if body.get("cache_prompt") and s["agent_cache"] else 0
            read, s["last_render"] = total - reused, render
        send({"choices": [{"delta": {}, "finish_reason": "stop"}]})
        send({"choices": [], "usage": {"completion_tokens": pieces, "secret_extra": "x"},
              **({} if o.get("no_timings") else {"timings": {"prompt_n": read, "cache_n": reused, "prompt_ms": 50.0,
                                                           "prompt_per_second": 2000.0, "predicted_n": pieces,
                                                           "predicted_ms": 5.0, "predicted_per_second": 200.0,
                                                           "private_case": "X"}})})
        if not o.get("no_done"):
            send("[DONE]")


class CoreTest(unittest.TestCase):
    def setUp(self):
        Fake.reset()
        self.srv = HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.tmp)

    def go(self, sizes=("medium",), origin="setup-default", label="t", cfg=CFG, identity=None, api_key=None, by=None, **kw):
        return core.run(self.base, cfg, origin, list(sizes), label, self.tmp, identity, api_key=api_key, by=by,
                        log=lambda *a: None, **kw)

    def doc(self, label="t"):
        return json.loads((self.tmp / f"{label}.result.json").read_text())

    # --- the normal path
    def test_full_run_identity_panel_fields_and_export(self):
        self.assertEqual(self.go(("probe", "medium")), 0)
        d = self.doc()
        self.assertEqual((d["label"], d["build_info"], d["model_path"], d["n_ctx"]),
                         ("t", "Strata 0.1.39", "Swift-IQ3_XXS-00001-of-00002.gguf", 32768))
        self.assertEqual(d["config"]["config_origin"], "setup-default")
        self.assertFalse(d["config"]["config_origin_verified"])
        self.assertEqual(d["config"]["args"][1], "<pack>")
        self.assertEqual(d["config"]["args"][3], "<native>")
        self.assertEqual(d["sizes"]["medium"]["prompt_tps"]["median"], 2000.0)
        self.assertEqual(d["sizes"]["probe"]["prompt_ms"]["median"], 50.0)
        self.assertNotIn("prompt_tps", d["sizes"]["probe"])
        self.assertEqual(len([r for r in d["runs"] if r["size"] == "probe"]), core.PROBE_CALLS)
        self.assertEqual(d["runs"][0]["reasoning_chars"], 3)
        self.assertEqual(d["runs"][0]["usage"], {"completion_tokens": 1})          # unknown usage key pruned
        self.assertNotIn("private_case", d["runs"][0]["timings"])                   # unknown timing key pruned
        self.assertEqual(d["validity"]["status"], "ok")                             # a normal probe is not "reused"
        self.assertEqual(validate.problems(d), [])

    def test_the_public_name_and_machine_id_are_optional_declared_and_validated(self):
        self.assertEqual(self.go(), 0)                                               # nobody is listed unless they ask
        self.assertNotIn("by", self.doc()["submission"])
        self.assertEqual(self.go(label="a", identity={"submission": {"by": "from-identity"}, "machine": {"machine_id": "rig-1"}}), 0)
        d = self.doc("a")
        self.assertEqual((d["submission"]["by"], d["machine"]["machine_id"]), ("from-identity", "rig-1"))
        self.assertEqual(validate.problems(d), [])
        self.assertEqual(self.go(label="b", by="cli-name", identity={"submission": {"by": "from-identity"}}), 0)
        self.assertEqual(self.doc("b")["submission"]["by"], "cli-name")              # --by wins over the identity file
        for bad in ("two words", "a" * 40, "/etc/passwd", "name@mail.com", "-leading", ""):
            with self.assertRaises(SystemExit, msg=bad):
                self.go(label="x", by=bad)
        with self.assertRaises(SystemExit):
            self.go(label="x", identity={"machine": {"machine_id": "my rig"}})
        d = self.doc("a")
        for block, key, value in (("submission", "by", "has space"), ("submission", "by", 5), ("submission", "by", ["x"]),
                                  ("machine", "machine_id", "x" * 40), ("machine", "machine_id", "a/b")):
            e = json.loads(json.dumps(d))
            e[block][key] = value
            self.assertIn(f"{block}.{key} must be a short name (letters, digits, '.', '_' and '-')", validate.problems(e), (key, value))
        self.assertEqual(self.go(label="c", identity={"submission": {"by": "n", "email": "x@y.z"}}), 0)
        self.assertNotIn("email", self.doc("c")["submission"])                       # keys outside the schema never get in

    def test_gprobe_three_sizes_distinct_excerpts_and_export(self):
        self.assertEqual(self.go(("gprobe",)), 0)
        d = self.doc()
        self.assertEqual(sorted(d["sizes"]), ["gprobe150", "gprobe300", "gprobe450"])
        calls = Fake.st["calls"]
        self.assertEqual(len(calls), 3 * core.PROBE_CALLS)
        self.assertTrue(all(c["messages"][0]["content"] == core.GPROBE_SYSTEM and c["max_tokens"] == 1 for c in calls))
        for n, size in enumerate(core.GPROBE_CHARS):
            users = [c["messages"][1]["content"] for c in calls[n * core.PROBE_CALLS:(n + 1) * core.PROBE_CALLS]]
            self.assertEqual({len(u) for u in users}, {core.GPROBE_CHARS[size]})
            self.assertEqual(len(set(users)), core.PROBE_CALLS)                      # a different excerpt per call
        self.assertEqual(d["sizes"]["gprobe300"]["read_median"], 20)
        self.assertEqual(validate.problems(d), [])
        d["protocol"]["gprobe"]["system_sha256"] = H64
        self.assertIn("gprobe is not standard-v1's", validate.problems(d))

    def test_unique_line_is_constant_per_size_and_run(self):
        self.go()
        firsts = [c["messages"][0]["content"].split("\n", 1)[0] for c in Fake.st["calls"]]
        self.assertEqual(firsts, ["[strata-bench medium warmup 1]", "[strata-bench medium run 1]",
                                  "[strata-bench medium run 2]", "[strata-bench medium run 3]"])
        self.assertTrue(all(c["cache_prompt"] is False for c in Fake.st["calls"]))
        d = self.doc()
        self.assertEqual(len({r["request_sha256"] for r in d["runs"]}), 4)

    # --- preflight
    def test_preflight_refusals_measure_nothing(self):
        cases = [("model", "E:\\m\\Other.gguf"), ("n_ctx", 65536), ("n_ctx", None), ("build", None),
                 ("model", "")]
        for i, (k, v) in enumerate(cases):
            Fake.reset()
            Fake.st[k] = v
            self.assertEqual(self.go(label=f"p{i}"), 2, (k, v))
            self.assertFalse((self.tmp / f"p{i}.result.json").exists())
            self.assertEqual(Fake.st["calls"], [])

    def test_refuses_overwrite(self):
        self.go()
        self.assertEqual(self.go(), 2)

    def test_api_key(self):
        Fake.st["api_key"] = "sk-secret-123456789"
        self.assertEqual(self.go(label="a"), 2)
        self.assertEqual(self.go(label="b", api_key="sk-secret-123456789"), 0)
        self.assertNotIn("sk-secret", (self.tmp / "b.result.json").read_text())

    def test_changed_prompt_refused(self):
        tmp = self.tmp / "prompts"
        shutil.copytree(core.PROMPTS, tmp)
        (tmp / "medium.txt").write_bytes((tmp / "medium.txt").read_bytes().replace(b"\n", b"\r\n", 1))
        with mock.patch.object(core, "PROMPTS", tmp):
            self.assertEqual(self.go(), 2)

    # --- measurement validity
    def test_failure_keeps_partial_result(self):
        Fake.st["fail_after"] = 2
        self.assertEqual(self.go(), 3)
        d = self.doc()
        self.assertEqual(d["validity"]["status"], "failed")                 # a 5xx from the server is a health failure
        self.assertEqual(d["validity"]["failure"], {"kind": "server_error", "where": "medium run 2", "http_status": 500})
        self.assertEqual(len(d["runs"]), 2)

    # --- the health view: catastrophic failures are exported as `failed`, with a closed kind and place
    def test_a_server_that_dies_mid_run_is_a_crash_and_keeps_what_was_measured(self):
        Fake.st["mode"], Fake.st["mode_after"] = "crash", 2
        self.assertEqual(self.go(), 3)
        d = self.doc()
        self.assertEqual((d["validity"]["status"], d["validity"]["failure"]["kind"], d["validity"]["failure"]["where"]), ("failed", "crash", "medium run 2"))
        self.assertEqual(len(d["runs"]), 2)
        self.assertEqual(validate.problems(d), [])                          # exportable as a health result

    def test_an_out_of_memory_answer_is_oom_and_the_servers_own_words_never_reach_the_result(self):
        Fake.st["mode"], Fake.st["mode_after"] = "oom", 1
        self.assertEqual(self.go(), 3)
        d = self.doc()
        f = d["validity"]["failure"]
        self.assertEqual((d["validity"]["status"], f["kind"], f["http_status"], f["where"]), ("failed", "oom", 500, "medium run 1"))
        self.assertNotIn("private", json.dumps(d))                          # the path in the server's message is not copied
        self.assertEqual(d["validity"]["error"], core.FAILURE_TEXT["oom"])
        self.assertEqual(validate.problems(d), [])

    def test_degenerate_output_is_garbage_and_the_run_that_produced_it_is_kept(self):
        Fake.st["mode"], Fake.st["mode_after"] = "garbage", 1
        self.assertEqual(self.go(), 3)
        d = self.doc()
        self.assertEqual((d["validity"]["status"], d["validity"]["failure"]["kind"]), ("failed", "garbage"))
        self.assertEqual(d["runs"][-1]["output_check"], "garbage")
        self.assertEqual(d["runs"][-2]["output_check"], "ok")
        self.assertEqual(validate.problems(d), [])

    def test_a_client_side_http_error_is_incomplete_not_a_health_failure(self):
        Fake.st["mode"], Fake.st["mode_after"] = "bad_request", 1
        self.assertEqual(self.go(), 3)
        self.assertEqual(self.doc()["validity"]["status"], "incomplete")
        self.assertTrue(validate.problems(self.doc()))                       # and it is not exportable

    def test_a_normal_run_marks_every_output_ok_and_has_no_failure(self):
        self.go()
        d = self.doc()
        self.assertEqual(d["validity"]["status"], "ok")
        self.assertNotIn("failure", d["validity"])
        self.assertTrue(all(r["output_check"] == "ok" for r in d["runs"]))

    def test_classification_of_exceptions(self):
        import http.client
        import socket
        import urllib.error
        c = core.classify_failure
        self.assertEqual(c(socket.timeout()), ("hang", None))
        self.assertEqual(c(TimeoutError()), ("hang", None))
        self.assertEqual(c(urllib.error.URLError(socket.timeout())), ("hang", None))
        self.assertEqual(c(urllib.error.URLError(ConnectionRefusedError())), ("crash", None))
        self.assertEqual(c(ConnectionResetError()), ("crash", None))
        self.assertEqual(c(http.client.RemoteDisconnected("x")), ("crash", None))
        self.assertEqual(c(http.client.IncompleteRead(b"x")), ("crash", None))
        self.assertEqual(c(core.MeasureFailure("garbage", "medium run 1")), ("garbage", None))
        self.assertEqual(c(core.MeasureError("no timings")), (None, None))
        self.assertEqual(c(ValueError("x")), (None, None))
        self.assertEqual(c(KeyError("x")), (None, None))

    def test_garbage_detector(self):
        g = core.looks_like_garbage
        self.assertTrue(g("tok " * 120))
        self.assertTrue(g("!!! ??? ... " * 10))
        self.assertFalse(g("def f(x):\n    return x + 1\n" * 3))              # repeated code lines are not degenerate: too few words
        self.assertFalse(g("The quick brown fox jumps over the lazy dog " * 6 + "and then some more varied words appear here"))
        self.assertFalse(g(""))
        self.assertFalse(g("ok"))

    def test_the_validator_gates_failed_results(self):
        Fake.st["mode"], Fake.st["mode_after"] = "crash", 1
        self.go()
        d = self.doc()
        self.assertEqual(validate.problems(d), [])
        bad = json.loads(json.dumps(d))
        bad["validity"]["failure"]["kind"] = "meltdown"
        self.assertTrue(any("failure.kind" in x for x in validate.problems(bad)))
        bad = json.loads(json.dumps(d))
        del bad["validity"]["failure"]
        self.assertTrue(any("failure.kind" in x for x in validate.problems(bad)))
        bad = json.loads(json.dumps(d))
        bad["validity"]["failure"]["where"] = "E:\\private\\x"
        self.assertTrue(validate.problems(bad))
        bad = json.loads(json.dumps(d))
        bad["validity"]["failure"]["where"] = "medium run 1; rm"
        self.assertTrue(any("failure.where" in x for x in validate.problems(bad)))
        ok = json.loads(json.dumps(d))
        ok["validity"].update(status="ok")
        self.assertTrue(any("only allowed on a failed" in x for x in validate.problems(ok)))
        inc = json.loads(json.dumps(d))
        inc["validity"].update(status="incomplete")
        self.assertTrue(any("not complete" in x for x in validate.problems(inc)))

    def test_ctrl_c_keeps_the_partial_result_marked_incomplete_not_running(self):
        Fake.st["fail_after"] = 2
        real = core.call
        calls = []

        def call(*a, **k):
            calls.append(1)
            if len(calls) == 3:
                raise KeyboardInterrupt
            return real(*a, **k)
        with mock.patch.object(core, "call", call):
            self.assertEqual(self.go(), 3)
        d = self.doc()
        self.assertEqual(d["validity"]["status"], "incomplete")        # never left as "running"
        self.assertIn("interrupted", d["validity"]["error"])
        self.assertEqual(len(d["runs"]), 2)                           # what was measured before the interrupt is kept

    def test_missing_timings_is_incomplete(self):
        Fake.st["no_timings"] = True
        self.assertEqual(self.go(), 3)
        self.assertEqual(self.doc()["validity"]["status"], "incomplete")

    def test_size_reuse_is_flagged(self):
        Fake.st["reused"] = 500
        self.go()
        self.assertEqual(self.doc()["validity"]["status"], "warnings")

    def test_probe_full_reuse_flagged(self):
        Fake.st["probe_read"], Fake.st["probe_reused"] = 0, 420
        self.go(("probe",))
        self.assertEqual(self.doc()["validity"]["status"], "warnings")

    # --- config identity
    def test_server_fields_keep_values_and_change_config_id(self):
        ids = {core.config_block({**CFG, "reasoning_budget_tokens": n}, "custom-1", False)["config_id"]
               for n in (0, 512, 4096)}
        self.assertEqual(len(ids), 3)
        b = core.config_block({**CFG, "fit_max_tokens": 1024}, "custom-1", False)
        self.assertEqual(b["server"]["fit_max_tokens"], 1024)
        a = core.config_block(CFG, "custom-1", False)["config_id"]
        self.assertEqual(a, core.config_block({**CFG, "port": 9, "log": "E:\\x.log"}, "custom-1", False)["config_id"])

    def test_env_secrets_redacted_engine_knobs_kept(self):
        cfg = {**CFG, "env": {"HF_TOKEN": "hf_abcdefghijk", "OPENAI_API_KEY": "x", "STRATA_API_KEY": "y",
                              "STRATA_INDEXER_PER_TOKEN": "1", "STRATA_GDN_KEYHEAD": "2"}}
        env = core.scrub_config(cfg)["env"]
        self.assertEqual([env[k] for k in ("HF_TOKEN", "OPENAI_API_KEY", "STRATA_API_KEY")], ["<redacted>"] * 3)
        self.assertEqual((env["STRATA_INDEXER_PER_TOKEN"], env["STRATA_GDN_KEYHEAD"]), ("1", "2"))

    def test_shared_settings_recorded_in_config_id(self):
        self.go(label="a")
        Fake.st["shared"] = {"reasoning_effort": "low", "unknown": "z"}
        self.go(label="b")
        a, b = self.doc("a"), self.doc("b")
        self.assertNotEqual(a["config"]["config_id"], b["config"]["config_id"])
        self.assertEqual(b["config"]["server_shared"], {"reasoning_effort": "low"})
        self.assertEqual(b["validity"]["status"], "warnings")

    def test_identity_cannot_override_observed_and_is_pruned(self):
        ident = {"engine": {"build_info": "Strata 0.1.32", "binary_sha256": H64},
                 "model": {"file": "OTHER.gguf", "n_ctx": 65536, "repo": "https://huggingface.co/u/m"},
                 "machine": {"ram_type": "DDR4", "hostname": "DESKTOP-1",
                             "gpus": [{"name": "RTX 5090", "private_case": "PRIVATE_CANARY"}]}}
        self.go(identity=ident)
        d = self.doc()
        self.assertEqual(d["engine"]["build_info"], "Strata 0.1.39")
        self.assertEqual(d["engine"]["binary_sha256"], H64)
        self.assertEqual(d["model"]["file"], "Swift-IQ3_XXS-00001-of-00002.gguf")
        self.assertEqual(d["machine"], {"ram_type": "DDR4", "gpus": [{"name": "RTX 5090"}]})
        self.assertNotIn("PRIVATE_CANARY", json.dumps(d))
        self.assertEqual(d["validity"]["status"], "warnings")
        self.assertEqual(validate.problems(d), [])                 # the HF URL is not an "absolute path"

    def test_bad_origin_rejected(self):
        with self.assertRaises(SystemExit):
            self.go(origin="tuned")

    # --- export gate
    def test_validator_rejects_private_and_leaks_at_any_depth(self):
        self.go()
        d = self.doc()
        bad = [{"note": "x"}, {"engine": {"exe": "E:\\strata\\strata.exe"}},
               {"machine": {"hostname": "DESKTOP-7Q2K1"}},
               {"machine": {"gpus": [{"name": "RTX 5090", "private_case": "P"}]}},
               {"sizes": {"medium": {**d["sizes"]["medium"], "private_case": "P"}}},
               {"runs": [{**d["runs"][0], "timings": {"private_case": "P"}}]},
               {"protocol": {**d["protocol"], "sampling": {"private_case": "P"}}},
               {"protocol": {**d["protocol"], "id": "private-x"}},
               {"protocol": {**d["protocol"], "prompt_sha256": {"medium.txt": H64}}},
               {"submission": {"label": "E:/strata-data/x"}}, {"model": {"file": "/opt/models/x.gguf"}},
               {"validity": {"status": "incomplete"}},
               {"config": {**d["config"], "env": {"K": "sk-live-abcdefghij"}}}]
        for b in bad:
            self.assertTrue(validate.problems({**d, **b}), b)
        self.assertEqual(validate.problems({**d, "machine": {"os": "Windows 10.0.26200"}}), [])

    def test_schema_prune_and_check_agree(self):
        v = {"gpus": [{"name": "a", "x": 1}], "ram_type": "DDR4", "zzz": 1}
        self.assertEqual(schema.check(schema.prune(v, schema.IDENTITY["machine"]), schema.IDENTITY["machine"]), [])

    def test_cli(self):
        cfg = self.tmp / "c.json"
        cfg.write_text(json.dumps(CFG))
        rc = main(["run", "--base", self.base, "--config", str(cfg), "--origin", "custom-2", "--label", "c",
                   "--out", str(self.tmp), "--sizes", "medium"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.doc("c")["config"]["config_origin"], "custom-2")
        self.assertEqual(main(["validate", str(self.tmp / "c.result.json")]), 0)


if __name__ == "__main__":
    unittest.main()
