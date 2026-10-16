"""standard-v2, phase 1: streamed requests, time to the first token, the additive v2 schema and its validator."""
import contextlib
import copy
import io
import itertools
import json
import shutil
import threading
import time
import unittest
import urllib.error
from unittest import mock

from strata_bench import core, schema, validate
from strata_bench.__main__ import main
from tests import test_core as TC
from tests.test_core import CFG, Fake

V2 = {"protocol": core.PROTOCOL_V2}


class V2Run(TC.CoreTest):
    """Reuses test_core's fake server and helpers; the inherited v1 tests are switched off here (test_core.py runs them)."""

    def v2(self, sizes=("medium",), label="v", **kw):
        return self.go(sizes, label=label, **V2, **kw)

    def sse(self, **o):
        Fake.st["sse"] = o


for _name in [n for n in dir(TC.CoreTest) if n.startswith("test_")]:
    setattr(V2Run, _name, None)


class V2Tests(V2Run):
    def test_a_v2_run_streams_measures_the_first_token_and_exports(self):
        self.assertEqual(self.v2(("probe", "medium")), 0)
        d = self.doc("v")
        self.assertEqual((d["schema"], d["protocol"]["id"]), ("strata-bench/v2", "standard-v2"))
        self.assertEqual((d["protocol"]["suite"], d["protocol"]["blocks"]), ("custom", ["first-token"]))
        self.assertEqual(d["runner"]["host_relation"], "same-host")                  # loopback --base
        medium = [r for r in d["runs"] if r["size"] == "medium"]
        self.assertEqual(len(medium), 4)
        for r in medium:
            self.assertEqual(r["stream"]["pieces"], 3)
            self.assertIsInstance(r["stream"]["ttft_ms"], float)
            self.assertLessEqual(r["stream"]["ttft_ms"], r["stream"]["total_ms"])
            self.assertEqual(r["wall_ms"], r["stream"]["total_ms"])
            self.assertEqual(r["tokens"], {"prompt": 100, "reused": 0, "generated": 3})
            self.assertEqual(r["timings"]["prompt_per_second"], 2000.0)             # speeds are the engine's own, from the stream
            self.assertNotIn("private_case", r["timings"])
            self.assertEqual(r["usage"], {"completion_tokens": 3})
        self.assertEqual(set(d["sizes"]["medium"]["ttft_ms"]), {"median", "min", "max"})
        self.assertEqual(d["sizes"]["medium"]["prompt_tps"]["median"], 2000.0)
        self.assertNotIn("ttft_ms", d["sizes"]["probe"])                              # probes stay v1-shaped calls
        self.assertTrue(all("stream" not in r for r in d["runs"] if r["size"] == "probe"))
        self.assertEqual(d["validity"]["status"], "ok")
        self.assertEqual(validate.problems(d), [])

    def test_the_request_is_streamed_with_thinking_off_and_the_prompt_read_fresh(self):
        self.v2()
        calls = Fake.st["calls"]
        self.assertEqual(len(calls), 4)
        for c in calls:
            self.assertIs(c["stream"], True)
            self.assertEqual(c["stream_options"], {"include_usage": True})
            self.assertEqual(c["reasoning_effort"], "none")
            self.assertEqual(c["chat_template_kwargs"], {"enable_thinking": False})
            self.assertIs(c["cache_prompt"], False)
            self.assertEqual((c["temperature"], c["seed"]), (0, 42))
        self.assertEqual(self.doc("v")["protocol"]["reasoning_sent"]["sizes"], "off (reasoning_effort none, enable_thinking false)")

    def test_v1_stays_what_it_was(self):
        self.assertEqual(self.go(("medium",), label="one"), 0)
        d = self.doc("one")
        self.assertEqual((d["schema"], d["protocol"]["id"]), ("strata-bench/v1", "standard-v1"))
        self.assertTrue(all("stream" not in c for c in Fake.st["calls"]))
        self.assertNotIn("host_relation", d["runner"])
        self.assertNotIn("suite", d["protocol"])
        self.assertNotIn("ttft_ms", d["sizes"]["medium"])

    def test_keepalives_and_empty_deltas_do_not_count_as_the_first_token(self):
        self.sse(first_delay=0.05, keepalive=True, keepalive_delay=0.15, empty_deltas=True)
        self.assertEqual(self.v2(), 0)
        for r in self.doc("v")["runs"]:
            self.assertGreaterEqual(r["stream"]["ttft_ms"], 180)                       # the first real text came after ~200 ms
            self.assertEqual(r["stream"]["pieces"], 3)                                 # empty deltas are not pieces

    def test_the_clock_starts_before_the_server_answers_not_when_the_headers_arrive(self):
        self.sse(header_delay=0.2)                                                     # the time a server spends reading the prompt
        self.assertEqual(self.v2(), 0)
        for r in self.doc("v")["runs"]:
            self.assertGreaterEqual(r["stream"]["ttft_ms"], 190)

    def test_the_gaps_between_pieces_are_recorded(self):
        self.sse(pieces=4, gap=0.06)
        self.assertEqual(self.v2(), 0)
        for r in self.doc("v")["runs"]:
            g = r["stream"]["gap_ms"]
            self.assertEqual(r["stream"]["pieces"], 4)
            self.assertGreaterEqual(g["p50"], 50)
            self.assertLessEqual(g["p50"], g["p95"])
            self.assertLessEqual(g["p95"], g["max"])

    def test_a_single_piece_has_no_gaps(self):
        self.sse(pieces=1)
        self.assertEqual(self.v2(), 0)
        self.assertIsNone(self.doc("v")["runs"][0]["stream"]["gap_ms"])

    def test_finish_reason_without_done_is_accepted(self):
        self.sse(no_done=True)
        self.assertEqual(self.v2(), 0)

    # --- the server failing while it streams is a health failure, with a closed kind and place
    def test_the_connection_closing_mid_stream_is_a_crash_and_keeps_what_was_measured(self):
        self.sse(drop_after=2)
        self.assertEqual(self.v2(), 3)
        d = self.doc("v")
        self.assertEqual(d["validity"]["status"], "failed")
        self.assertEqual(d["validity"]["failure"], {"kind": "crash", "where": "medium warmup 1", "http_status": None})
        self.assertEqual(len(d["runs"]), 0)                                            # the run in flight never completed
        self.assertEqual(validate.problems(d), [])                                     # a failed v2 result is publishable

    def test_an_error_event_in_the_stream_is_a_failure_and_the_servers_words_are_not_kept(self):
        self.sse(error="CUDA error: out of memory at E:\\private\\kernel.cu")
        self.assertEqual(self.v2(), 3)
        d = self.doc("v")
        self.assertEqual(d["validity"]["failure"]["kind"], "oom")
        self.assertNotIn("private", json.dumps(d))
        self.assertEqual(validate.problems(d), [])
        Fake.reset()
        self.sse(error="internal error in the scheduler")
        self.assertEqual(self.v2(label="w"), 3)
        self.assertEqual(self.doc("w")["validity"]["failure"]["kind"], "server_error")

    def test_a_failure_after_some_runs_keeps_them_and_names_the_place(self):
        Fake.st["mode"], Fake.st["mode_after"] = "oom", 2                              # warmup and run 1 answer, run 2 is refused
        self.assertEqual(self.v2(), 3)
        d = self.doc("v")
        self.assertEqual(d["validity"]["failure"]["where"], "medium run 2")
        self.assertEqual(len(d["runs"]), 2)

    def test_garbage_in_the_stream_is_caught_like_in_v1(self):
        self.sse(garbage=True)
        self.assertEqual(self.v2(), 3)
        d = self.doc("v")
        self.assertEqual((d["validity"]["status"], d["validity"]["failure"]["kind"]), ("failed", "garbage"))
        self.assertEqual((d["validity"]["failure"]["where"], len(d["runs"])), ("medium warmup 1", 1))   # the run that produced it is kept
        self.assertEqual(d["runs"][0]["output_check"], "garbage")
        self.assertEqual(validate.problems(d), [])

    def test_no_timings_in_the_stream_is_incomplete_not_a_pass(self):
        self.sse(no_timings=True)
        self.assertEqual(self.v2(), 3)
        d = self.doc("v")
        self.assertEqual(d["validity"]["status"], "incomplete")
        self.assertIn("no_stream_timings", d["validity"]["error"])
        self.assertTrue(validate.problems(d))                                         # incomplete results are not publishable

    # --- where the client ran
    def test_host_relation_is_required_off_loopback_and_closed(self):
        with self.assertRaises(SystemExit):
            core.run("http://192.0.2.1:9", CFG, "setup-default", ["medium"], "x", self.tmp, protocol=core.PROTOCOL_V2, log=lambda *a: None)
        with self.assertRaises(SystemExit):
            self.v2(host_relation="somewhere")
        with self.assertRaises(SystemExit):
            self.go(label="x", host_relation="lan")                                    # v1 has no host relation
        self.assertFalse(list(self.tmp.glob("x*")))
        self.assertEqual(self.v2(label="lan", host_relation="lan"), 0)
        self.assertEqual(self.doc("lan")["runner"]["host_relation"], "lan")
        self.assertEqual(validate.problems(self.doc("lan")), [])

    def test_loopback_detection(self):
        for b in ("http://127.0.0.1:8080", "http://localhost:1", "http://[::1]:5", "http://127.1.2.3"):
            self.assertTrue(core.is_loopback(b), b)
        for b in ("http://192.168.0.5:80", "http://example.com", "http://127.example.com", "http://10.0.0.1"):
            self.assertFalse(core.is_loopback(b), b)

    def test_unknown_protocol_is_refused(self):
        with self.assertRaises(SystemExit):
            self.go(label="x", protocol="standard-v9")

    # --- the validator
    def test_percentile_is_nearest_rank(self):
        self.assertEqual(core.percentile([1, 2, 3], 50), 2)
        self.assertEqual(core.percentile(list(range(1, 21)), 95), 19)
        self.assertEqual(core.percentile([5], 95), 5)

    def test_validator_pairs_schema_with_protocol_and_gates_the_v2_fields(self):
        self.v2()
        self.go(label="one")
        v2, v1 = self.doc("v"), self.doc("one")
        self.assertEqual(validate.problems(v1), [])
        self.assertEqual(validate.problems(v2), [])

        def broken(base, fn):
            d = copy.deepcopy(base)
            fn(d)
            return validate.problems(d)
        self.assertIn("protocol standard-v2 is written in strata-bench/v2, not strata-bench/v1",
                      broken(v2, lambda d: d.update(schema="strata-bench/v1")))
        self.assertIn("protocol standard-v1 is written in strata-bench/v1, not strata-bench/v2",
                      broken(v1, lambda d: d.update(schema="strata-bench/v2")))
        self.assertIn("schema is not strata-bench/v1 or strata-bench/v2", broken(v2, lambda d: d.update(schema="strata-bench/v3")))
        self.assertIn("protocol 'standard-v3' is not public", broken(v2, lambda d: d["protocol"].update(id="standard-v3")))
        self.assertIn("protocol (value not shown) is not public", broken(v2, lambda d: d["protocol"].update(id=["standard-v2"])))
        for field, fn in (("runner.host_relation", lambda d: d["runner"].update(host_relation="cloud")),
                          ("runner.host_relation", lambda d: d["runner"].pop("host_relation")),
                          ("protocol.suite", lambda d: d["protocol"].update(suite="everything")),
                          ("protocol.suite", lambda d: d["protocol"].pop("suite")),
                          ("protocol.blocks", lambda d: d["protocol"].update(blocks=[])),
                          ("protocol.blocks", lambda d: d["protocol"].update(blocks=["first-token", "free-text"])),
                          ("protocol.blocks", lambda d: d["protocol"].update(blocks="first-token"))):
            self.assertTrue(any(p.startswith(field + " must be") for p in broken(v2, fn)), field)
        for field, fn in (("runner.host_relation", lambda d: d["runner"].update(host_relation="lan")),
                          ("protocol.suite", lambda d: d["protocol"].update(suite="custom")),
                          ("protocol.blocks", lambda d: d["protocol"].update(blocks=["first-token"])),
                          ("runs[].stream", lambda d: d["runs"][0].update(stream={"ttft_ms": 1})),
                          ("sizes.*.ttft_ms", lambda d: d["sizes"]["medium"].update(ttft_ms={"median": 1}))):
            self.assertIn(f"{field} is only allowed in strata-bench/v2", broken(v1, fn), field)
        # closed schema: nothing free-form can ride in the new fields
        self.assertTrue(broken(v2, lambda d: d["runs"][0]["stream"].update(note="x")))
        self.assertTrue(broken(v2, lambda d: d["runs"][0]["stream"].update(gap_ms={"p50": 1, "p99": 2})))
        self.assertTrue(broken(v2, lambda d: d["runs"][0]["stream"].update(ttft_ms=[1])))
        self.assertTrue(broken(v2, lambda d: d["sizes"]["medium"].update(ttft_ms={"mean": 1})))

    def test_prompt_hashes_are_the_same_in_v2(self):
        self.v2()
        d = self.doc("v")
        self.assertEqual(d["protocol"]["prompt_sha256"], {**schema.STANDARD_V1_PROMPTS, **schema.STANDARD_V2_PROMPTS})   # the v1 texts, unchanged, and the agent files
        d["protocol"]["prompt_sha256"]["medium.txt"] = "ab" * 32
        self.assertIn("prompt medium.txt is not standard-v2's", validate.problems(d))

    def test_cli_v2(self):
        cfg = self.tmp / "c.json"
        cfg.write_text(json.dumps(CFG))
        rc = main(["run", "--base", self.base, "--config", str(cfg), "--origin", "setup-default", "--label", "c",
                   "--out", str(self.tmp), "--suite", "quick", "--protocol", "standard-v2", "--host-relation", "same-host"])
        self.assertEqual(rc, 0)
        self.assertEqual(main(["validate", str(self.tmp / "c.result.json")]), 0)
        with self.assertRaises(SystemExit):
            main(["run", "--base", self.base, "--config", str(cfg), "--origin", "setup-default", "--label", "d",
                  "--out", str(self.tmp), "--protocol", "standard-v2", "--host-relation", "moon"])


class AgentTests(V2Run):
    def agent(self, label="a", **kw):
        return self.go(("agent",), label=label, **V2, **kw)

    def small_cfg(self, ctx):
        return {**CFG, "args": [str(ctx) if a == "32768" else a for a in CFG["args"]]}

    def test_an_agent_run_has_five_turns_three_sessions_and_a_warmup_and_exports(self):
        self.assertEqual(self.agent(), 0)
        d = self.doc("a")
        P = schema.AGENT_PROTOCOL
        runs = [r for r in d["runs"] if r["size"] == "agent"]
        self.assertEqual(len(runs), (P["warmup"] + P["sessions"]) * P["turns"])
        self.assertEqual([r["turn"] for r in runs[:5]], [1, 2, 3, 4, 5])
        self.assertEqual({(r["kind"], r["index"]) for r in runs}, {("warmup", 1), ("run", 1), ("run", 2), ("run", 3)})
        self.assertEqual((d["protocol"]["blocks"], d["protocol"]["agent"]), (["agent"], P))
        self.assertNotIn("skipped", d["protocol"])
        ag = d["agent"]
        self.assertEqual([x["index"] for x in ag["sessions"]], [1, 2, 3])
        for x in ag["sessions"]:
            self.assertGreater(x["hit_pct"], 95)                                      # the fake keeps the whole earlier conversation
            self.assertLess(x["reread_tokens"], 20)
        self.assertEqual(sorted(ag["turns"]), ["1", "2", "3", "4", "5"])
        self.assertLess(ag["turns"]["1"]["cache_n"]["max"], 50)                       # the marker line keeps sessions apart
        self.assertGreater(ag["turns"]["5"]["cache_n"]["median"], ag["turns"]["2"]["cache_n"]["median"])
        self.assertLess(ag["turns"]["5"]["prompt_n"]["median"], ag["turns"]["1"]["prompt_n"]["median"])   # later turns read only what is new
        self.assertEqual(d["validity"]["status"], "ok")
        self.assertEqual(validate.problems(d), [])
        self.assertTrue(all(set(r["stream"]) == {"ttft_ms", "total_ms", "pieces", "gap_ms"} for r in runs))

    def test_the_agent_requests_carry_the_fixed_texts_tools_and_the_models_own_answers(self):
        self.agent()
        calls = Fake.st["calls"]
        self.assertEqual(len(calls), 20)
        tools = json.loads((core.PROMPTS / "agent_tools.json").read_text())
        for c in calls:
            self.assertEqual([t["function"]["name"] for t in c["tools"]], ["read_file", "search", "edit_file", "run_command"])
            self.assertEqual(c["tools"], tools)
            self.assertEqual((c["tool_choice"], c["cache_prompt"], c["max_tokens"], c["stream"]), ("none", True, 256, True))
        last = calls[9]["messages"]                                                    # warmup 1 is calls 0 to 4, run 1 is 5 to 9
        self.assertEqual([m["role"] for m in last], ["system"] + ["user", "assistant"] * 4 + ["user"])
        self.assertTrue(last[0]["content"].startswith("[strata-bench agent run 1]\n"))
        self.assertEqual(last[0]["content"].split("\n", 1)[1], (core.PROMPTS / "agent_system.txt").read_text())
        self.assertTrue(last[1]["content"].startswith("File 1 of 5:\n\n"))
        self.assertIn((core.PROMPTS / "agent_doc_3.txt").read_text(), last[5]["content"])
        self.assertEqual(last[2]["content"], "SAFE SAFE SAFE")                          # what the server answered goes back, verbatim
        self.assertEqual(len({c["messages"][0]["content"] for c in calls}), 4)         # one marker per session

    def test_a_cache_that_does_not_work_is_visible_and_warned_about(self):
        Fake.st["agent_cache"] = False
        self.assertEqual(self.agent(), 0)
        d = self.doc("a")
        for x in d["agent"]["sessions"]:
            self.assertEqual(x["hit_pct"], 0)
            self.assertGreater(x["reread_tokens"], 1000)
        self.assertEqual(d["validity"]["status"], "warnings")
        self.assertTrue(any("agent run 1: only 0.0% of the earlier conversation came from the cache" in w for w in d["validity"]["warnings"]))
        self.assertEqual(validate.problems(d), [])                                     # slow is not invalid

    def test_a_context_that_is_too_small_skips_the_block_with_a_reason(self):
        Fake.st["n_ctx"] = 8192
        self.assertEqual(self.go(("medium", "agent"), label="s", cfg=self.small_cfg(8192), **V2), 0)
        d = self.doc("s")
        self.assertEqual(d["protocol"]["blocks"], ["first-token", "agent"])
        self.assertEqual(d["protocol"]["skipped"], [{"block": "agent", "reason": "context_too_small"}])
        self.assertNotIn("agent", d)
        self.assertNotIn("agent", d["protocol"])
        self.assertFalse([r for r in d["runs"] if r["size"] == "agent"])
        self.assertEqual(validate.problems(d), [])
        Fake.st["calls"].clear()
        self.assertEqual(self.go(("agent",), label="only", cfg=self.small_cfg(8192), **V2), 2)     # nothing would be measured
        self.assertFalse((self.tmp / "only.result.json").exists())
        self.assertEqual(Fake.st["calls"], [])

    def test_the_context_limit_is_exact(self):
        for ctx, ran in ((schema.AGENT_MIN_CTX, True), (schema.AGENT_MIN_CTX - 1, False)):
            Fake.reset()
            Fake.st["n_ctx"] = ctx
            self.assertEqual(self.go(("medium", "agent"), label=f"c{ctx}", cfg=self.small_cfg(ctx), **V2), 0)
            d = self.doc(f"c{ctx}")
            self.assertEqual("agent" in d, ran, ctx)
            self.assertEqual(d["protocol"].get("skipped"), None if ran else [{"block": "agent", "reason": "context_too_small"}])

    def test_a_failure_in_the_middle_of_a_session_names_the_turn_and_stays_publishable(self):
        Fake.st["mode"], Fake.st["mode_after"] = "oom", 7
        self.assertEqual(self.agent(), 3)
        d = self.doc("a")
        self.assertEqual(d["validity"]["failure"], {"kind": "oom", "where": "agent run 1 turn 3", "http_status": 500})
        self.assertEqual(len(d["runs"]), 7)
        self.assertNotIn("agent", d)
        self.assertEqual(validate.problems(d), [])

    def test_degenerate_agent_output_is_a_garbage_failure_at_that_turn(self):
        self.sse(garbage=True)
        self.assertEqual(self.agent(), 3)
        d = self.doc("a")
        self.assertEqual(d["validity"]["failure"], {"kind": "garbage", "where": "agent warmup 1 turn 1", "http_status": None})
        self.assertEqual(len(Fake.st["calls"]), 1)                                     # it stops at the first bad answer
        self.assertEqual(validate.problems(d), [])

    def test_an_empty_answer_is_incomplete_not_a_pass(self):
        self.sse(pieces=0)
        self.assertEqual(self.agent(), 3)
        self.assertEqual(self.doc("a")["validity"]["status"], "incomplete")

    def test_the_agent_block_is_only_for_v2_and_v2_needs_something_to_measure(self):
        with self.assertRaises(SystemExit):
            self.go(("agent",), label="x")
        with self.assertRaises(SystemExit):
            self.go(("probe",), label="x", **V2)
        self.assertFalse(list(self.tmp.glob("x*")))
        self.assertEqual(Fake.st["calls"], [])

    def test_a_changed_agent_file_refuses_the_run(self):
        tmp = self.tmp / "prompts"
        shutil.copytree(core.PROMPTS, tmp)
        (tmp / "agent_doc_2.txt").write_bytes((tmp / "agent_doc_2.txt").read_bytes() + b" ")
        with mock.patch.object(core, "PROMPTS", tmp):
            self.assertEqual(self.agent(), 2)
            self.assertEqual(self.go(("medium",), label="v1ok"), 0)                    # v1 does not read the agent files
        self.assertEqual(Fake.st["calls"][0]["messages"][0]["role"], "user")

    def test_the_committed_agent_files_are_what_the_generator_makes(self):
        import sys
        sys.path.insert(0, str(core.PROMPTS.parent.parent / "tools"))
        import make_agent_prompts
        self.assertEqual(make_agent_prompts.main(["--check"]), 0)
        for name, h in schema.STANDARD_V2_PROMPTS.items():
            self.assertEqual(core.sha256((core.PROMPTS / name).read_bytes()), h, name)
        self.assertTrue(all(len(t.read_text()) <= 7000 for t in core.PROMPTS.glob("agent_doc_*.txt")))

    def test_session_stats(self):
        def row(read, cache, gen):
            return {"tokens": {"prompt": read, "reused": cache, "generated": gen}}
        none = core.session_stats(1, [row(100, 0, 10), row(210, 0, 10), row(330, 0, 10)])
        self.assertEqual((none["hit_pct"], none["reread_tokens"], none["new_tokens"]), (0.0, 110 + 220, 100 + 110))
        full = core.session_stats(2, [row(100, 0, 10), row(100, 110, 10), row(100, 220, 10)])
        self.assertEqual((full["hit_pct"], full["reread_tokens"], full["new_tokens"]), (100.0, 0, 200))
        self.assertIsNone(core.session_stats(3, [row(100, 0, 10)])["hit_pct"])           # one turn has no past to reuse
        over = core.session_stats(4, [row(10, 0, 0), row(10, 999, 0)])
        self.assertEqual(over["hit_pct"], 100.0)                                         # never above 100

    def test_validator_gates_the_agent_fields(self):
        self.agent()
        self.go(("medium",), label="m", **V2)
        a, m = self.doc("a"), self.doc("m")

        def broken(base, fn):
            d = copy.deepcopy(base)
            fn(d)
            return validate.problems(d)
        self.assertIn("a result with the agent block needs agent.sessions", broken(a, lambda d: d.pop("agent")))
        self.assertIn("a result with the agent block needs agent.sessions", broken(a, lambda d: d["agent"].update(sessions=[])))
        self.assertIn("agent data is only allowed when the agent block ran", broken(m, lambda d: d.update(agent=copy.deepcopy(a["agent"]))))
        self.assertIn("agent data is only allowed when the agent block ran", broken(m, lambda d: d["runs"][0].update(size="agent")))
        self.assertIn("protocol.agent is not standard-v2's", broken(a, lambda d: d["protocol"]["agent"].update(turns=4)))
        self.assertIn("protocol.agent is not standard-v2's", broken(a, lambda d: d["protocol"]["agent"].update(warmup=True)))
        self.assertIn("protocol.agent is not standard-v2's", broken(a, lambda d: d["protocol"].pop("agent")))
        self.assertTrue(any("skipped must list" in p for p in broken(a, lambda d: d["protocol"].update(skipped=[{"block": "agent", "reason": "slow"}]))))
        self.assertTrue(any("skipped must list" in p for p in broken(m, lambda d: d["protocol"].update(skipped=[{"block": "chat", "reason": "context_too_small"}]))))
        self.assertTrue(any("not in the public schema" in p for p in broken(a, lambda d: d["agent"].update(note="x"))))
        self.assertTrue(any("not in the public schema" in p for p in broken(a, lambda d: d["agent"]["sessions"][0].update(text="x"))))
        self.assertTrue(any("key not allowed" in p for p in broken(a, lambda d: d["agent"]["turns"].update({"6": {}}))))
        self.assertIn("prompt agent_doc_1.txt is not standard-v2's", broken(a, lambda d: d["protocol"]["prompt_sha256"].update({"agent_doc_1.txt": "0" * 64})))
        self.assertIn("prompt agent_doc_9.txt is not standard-v2's", broken(a, lambda d: d["protocol"]["prompt_sha256"].update({"agent_doc_9.txt": "0" * 64})))
        for fld in ("protocol.skipped", "protocol.agent", "agent"):                      # none of it exists in v1
            def v1(d, fld=fld):
                d.update(schema="strata-bench/v1")
                d["protocol"]["id"] = "standard-v1"
                for k in ("suite", "blocks"):
                    d["protocol"].pop(k, None)
                d["runner"].pop("host_relation", None)
                for r in d["runs"]:
                    r.pop("stream", None)
                    r.pop("turn", None)
                d["sizes"].pop("ttft_ms", None)
                if fld == "protocol.skipped":
                    d["protocol"]["skipped"] = []
                    d["protocol"].pop("agent", None)
                    d.pop("agent", None)
                elif fld == "protocol.agent":
                    d.pop("agent", None)
                else:
                    d["protocol"].pop("agent", None)
            self.assertIn(f"{fld} is only allowed in strata-bench/v2", broken(a, v1), fld)
        self.assertIn("prompt agent_system.txt is not standard-v1's", broken(a, lambda d: (d.update(schema="strata-bench/v1"), d["protocol"].update(id="standard-v1"))[0]))
        for where, ok in (("agent run 2 turn 3", True), ("agent warmup 1 turn 5", True), ("agent run 2 turn 6", False), ("agent run 2", False),
                          ("agent run x turn 1", False), ("agent run 1 turn 1; rm", False)):
            f = copy.deepcopy(a)
            f["validity"].update(status="failed", failure={"kind": "crash", "where": where})
            self.assertEqual("failure.where is not one of the protocol's call names" not in validate.problems(f), ok, where)


class ChatTests(V2Run):
    def chat(self, label="c", sizes=("chat",), **kw):
        return self.go(sizes, label=label, **V2, **kw)

    def test_a_chat_run_is_nine_short_answers_capped_at_a_hundred_tokens(self):
        self.assertEqual(self.chat(), 0)
        d = self.doc("c")
        P = schema.CHAT_PROTOCOL
        runs = [r for r in d["runs"] if r["size"] == "chat"]
        self.assertEqual((len(runs), [r["kind"] for r in runs].count("run")), (P["warmup"] + P["runs"], P["runs"]))
        calls = Fake.st["calls"]
        short = (core.PROMPTS / "short.txt").read_text()
        self.assertEqual(calls[3]["messages"][0]["content"], "[strata-bench chat run 3]\n" + short)
        for c in calls:
            self.assertEqual((c["max_tokens"], c["stream"], c["cache_prompt"], c["reasoning_effort"]), (100, True, False, "none"))
        self.assertEqual((d["protocol"]["blocks"], d["protocol"]["chat"]), (["chat"], P))
        self.assertEqual(set(d["sizes"]["chat"]), {"prompt_tps", "decode_tps", "prompt_ms", "ttft_ms"})
        self.assertEqual(d["validity"]["status"], "ok")
        self.assertEqual(validate.problems(d), [])

    def test_blocks_are_listed_in_a_fixed_order_next_to_the_text_sizes(self):
        self.assertEqual(self.chat(sizes=("clients", "agent", "chat", "medium"), label="all", host_relation="lan",
                                   by=None), 0)
        self.assertEqual(self.doc("all")["protocol"]["blocks"], ["first-token", "chat", "agent", "clients"])

    def test_chat_is_v2_only_and_failures_name_the_call(self):
        with self.assertRaises(SystemExit):
            self.go(("chat",), label="x")
        Fake.st["mode"], Fake.st["mode_after"] = "oom", 3
        self.assertEqual(self.chat(label="f"), 3)
        d = self.doc("f")
        self.assertEqual(d["validity"]["failure"]["where"], "chat run 3")
        self.assertEqual(validate.problems(d), [])


class ClientsTests(V2Run):
    def clients(self, label="k", sizes=("clients",), **kw):
        return self.go(sizes, label=label, **V2, **kw)

    def test_clients_send_at_once_and_the_rows_carry_the_throughput_and_percentiles(self):
        self.sse(first_delay=0.1, gap=0.04)                                            # first token at ~0.1 s, the whole answer at ~0.18 s
        t0 = time.perf_counter()
        self.assertEqual(self.clients(), 0)
        elapsed = time.perf_counter() - t0
        d = self.doc("k")
        P = schema.CLIENTS_PROTOCOL
        self.assertLess(elapsed, 4.0)                                                  # 42 requests of 0.18 s: serial would be over 7 s
        self.assertEqual(Fake.st["max_active"], 8)                                     # the eight clients were really in flight together
        self.assertEqual((d["protocol"]["blocks"], d["protocol"]["clients"]), (["clients"], P))
        self.assertEqual([x["n"] for x in d["clients"]], [2, 4, 8])
        for row in d["clients"]:
            self.assertEqual((row["rounds"], row["requests"], row["errors"]), (2, 2 * row["n"], 0))
            self.assertGreaterEqual(row["ttft_p50_ms"], 90)
            self.assertLessEqual(row["ttft_p50_ms"], row["ttft_p95_ms"])
            self.assertLessEqual(row["ttft_p95_ms"], row["latency_p95_ms"])
            self.assertLessEqual(row["total_tps"], row["n"] * 3 / 0.18)                # 3 tokens per client, over at least 0.18 s
            self.assertGreater(row["total_tps"], row["n"] * 3 * 1.5)                   # and not over seconds: tokens are divided by the time
            self.assertGreaterEqual(row["latency_p50_ms"], row["ttft_p50_ms"] + 60)    # the whole request takes longer than its first token
        runs = [r for r in d["runs"] if r["size"] == "clients"]
        self.assertEqual(len(runs), (2 + 4 + 8) * 3)
        self.assertEqual(len({r["request_sha256"] for r in runs}), len(runs))        # every client sent its own first line
        calls = [c for c in Fake.st["calls"]]
        self.assertTrue(all(c["max_tokens"] == 128 and c["cache_prompt"] is False and c["stream"] is True for c in calls))
        self.assertIn("[strata-bench clients 4 run 2 3]\n", {c["messages"][0]["content"].split("\n", 1)[0] + "\n" for c in calls})
        self.assertEqual(d["validity"]["status"], "ok")
        self.assertEqual(validate.problems(d), [])

    def test_a_429_is_counted_and_warned_about_but_a_503_is_a_server_failure(self):
        Fake.st["busy_client"], Fake.st["busy_status"] = "2", 429
        self.assertEqual(self.clients(label="b429"), 0)
        d = self.doc("b429")
        self.assertEqual([x["errors"] for x in d["clients"]], [2, 2, 2])           # client 2 is turned away in both measured rounds of each N
        self.assertEqual([x["requests"] for x in d["clients"]], [4, 8, 16])
        busy = [r for r in d["runs"] if "error_status" in r]
        self.assertTrue(busy and all(r["error_status"] == 429 and "stream" not in r and "timings" not in r for r in busy))
        self.assertEqual(d["validity"]["status"], "warnings")                         # fewer clients than N were served in some rounds: say so
        self.assertEqual(len([w for w in d["validity"]["warnings"] if "request(s) turned away as busy, so the total" in w]), 3)       # the measured rounds
        self.assertEqual(len([w for w in d["validity"]["warnings"] if "warm-up request(s) turned away" in w]), 3)
        self.assertEqual(validate.problems(d), [])
        Fake.reset()
        Fake.st["busy_client"], Fake.st["busy_status"] = "2", 503                    # Strata answers 503 while its engine restarts or after it died
        self.assertEqual(self.clients(label="b503"), 3)
        d = self.doc("b503")
        self.assertEqual(d["validity"]["failure"], {"kind": "server_error", "where": "clients 2 warmup 1", "http_status": 503})
        self.assertEqual(validate.problems(d), [])

    def test_when_every_request_is_turned_away_the_row_is_empty_and_warned_about(self):
        Fake.st["busy_client"], Fake.st["busy_status"] = "*", 429
        self.assertEqual(self.clients(), 0)
        d = self.doc("k")
        self.assertEqual(d["clients"][0], {"n": 2, "rounds": 2, "requests": 4, "errors": 4, "total_tps": None, "ttft_p50_ms": None,
                                           "ttft_p95_ms": None, "latency_p50_ms": None, "latency_p95_ms": None})
        self.assertEqual(d["validity"]["status"], "warnings")
        self.assertIn("clients 2: every request was turned away as busy", d["validity"]["warnings"])
        self.assertEqual(validate.problems(d), [])

    def test_any_other_failure_of_a_client_is_a_failure_of_the_server(self):
        Fake.st["mode"], Fake.st["mode_after"] = "oom", 0
        self.assertEqual(self.clients(label="o"), 3)
        d = self.doc("o")
        self.assertEqual(d["validity"]["failure"]["kind"], "oom")
        self.assertEqual(d["validity"]["failure"]["where"], "clients 2 warmup 1")
        self.assertEqual(validate.problems(d), [])
        self.sse(drop_after=1)
        Fake.st["mode"] = None
        self.assertEqual(self.clients(label="d"), 3)
        self.assertEqual(self.doc("d")["validity"]["failure"], {"kind": "crash", "where": "clients 2 warmup 1", "http_status": None})
        Fake.st["busy_client"], Fake.st["busy_status"] = "1", 400                      # a client-side error is not the server's health
        self.sse()
        self.assertEqual(self.clients(label="e"), 3)
        self.assertEqual(self.doc("e")["validity"]["status"], "incomplete")

    def test_a_failure_in_a_measured_round_names_the_round_and_keeps_earlier_rows(self):
        Fake.st["mode"], Fake.st["mode_after"] = "oom", 2 * 3 + 1                      # N=2 finishes (6 requests); the first N=4 request fails
        self.assertEqual(self.clients(), 3)
        d = self.doc("k")
        self.assertEqual(d["validity"]["failure"]["where"], "clients 4 warmup 1")
        self.assertGreaterEqual(len([r for r in d["runs"] if r["clients"] == 2]), 6)
        self.assertNotIn("clients", d)

    def test_no_parallel_slots_skips_the_block_and_unknown_slots_run(self):
        Fake.st["slots"] = 1
        self.assertEqual(self.clients(label="s", sizes=("medium", "clients")), 0)
        d = self.doc("s")
        self.assertEqual(d["protocol"]["skipped"], [{"block": "clients", "reason": "no_parallel_slots"}])
        self.assertEqual(d["protocol"]["blocks"], ["first-token", "clients"])
        self.assertNotIn("clients", d)
        self.assertNotIn("clients", d["protocol"])
        self.assertEqual(validate.problems(d), [])
        self.assertEqual(self.clients(label="only"), 2)
        self.assertFalse((self.tmp / "only.result.json").exists())
        Fake.st["slots"] = 2
        self.assertEqual(self.clients(label="two"), 0)                                  # more clients than slots just queue: still a measurement
        Fake.st["slots"] = None
        self.assertEqual(self.clients(label="unknown"), 0)

    def test_the_clients_block_is_v2_only(self):
        with self.assertRaises(SystemExit):
            self.go(("clients",), label="x")

    def test_validator_gates_chat_and_clients(self):
        self.clients()
        self.go(("chat",), label="c", **V2)
        self.go(("medium",), label="m", **V2)
        k, c, m = self.doc("k"), self.doc("c"), self.doc("m")

        def broken(base, fn):
            d = copy.deepcopy(base)
            fn(d)
            return validate.problems(d)
        self.assertIn("a result with the clients block needs clients", broken(k, lambda d: d.pop("clients")))
        self.assertIn("a result with the clients block needs clients", broken(k, lambda d: d.update(clients=[])))
        self.assertIn("a result with the chat block needs sizes.chat", broken(c, lambda d: d["sizes"].pop("chat")))
        self.assertIn("clients data is only allowed when the clients block ran", broken(m, lambda d: d.update(clients=copy.deepcopy(k["clients"]))))
        self.assertIn("chat data is only allowed when the chat block ran", broken(m, lambda d: d["sizes"].update(chat=copy.deepcopy(c["sizes"]["chat"]))))
        self.assertIn("chat data is only allowed when the chat block ran", broken(m, lambda d: d["protocol"].update(chat=dict(schema.CHAT_PROTOCOL))))
        self.assertIn("protocol.clients is not standard-v2's", broken(k, lambda d: d["protocol"]["clients"].update(counts="2,4")))
        self.assertIn("protocol.clients is not standard-v2's", broken(k, lambda d: d["protocol"]["clients"].update(rounds=True)))
        self.assertIn("protocol.chat is not standard-v2's", broken(c, lambda d: d["protocol"]["chat"].update(max_tokens=256)))
        self.assertIn("protocol.chat is not standard-v2's", broken(c, lambda d: d["protocol"].pop("chat")))
        self.assertTrue(any("not in the public schema" in p for p in broken(k, lambda d: d["clients"][0].update(note="x"))))
        self.assertTrue(any("not a number" in p for p in broken(k, lambda d: d["clients"][0].update(errors=[1]))))
        for fld in ("protocol.chat", "sizes.chat", "protocol.clients", "clients", "runs[].clients", "runs[].client", "runs[].error_status"):
            def v1(d, fld=fld):
                d.update(schema="strata-bench/v1")
                d["protocol"].update(id="standard-v1")
                for key in ("suite", "blocks", "skipped", "agent", "chat", "clients"):
                    d["protocol"].pop(key, None)
                d["runner"].pop("host_relation", None)
                d.pop("clients", None)
                d["sizes"].pop("chat", None)
                for r in d["runs"]:
                    for key in schema.V2_RUN_KEYS:
                        r.pop(key, None)
                for sz in d["sizes"].values():
                    sz.pop("ttft_ms", None)
                if fld in ("protocol.chat", "protocol.clients"):
                    d["protocol"][fld.split(".")[1]] = {"x": 1}
                elif fld == "sizes.chat":
                    d["sizes"]["chat"] = {"decode_tps": {"median": 1}}
                elif fld == "clients":
                    d["clients"] = []
                else:
                    d["runs"][0][fld.split(".")[1]] = 1
            self.assertIn(f"{fld} is only allowed in strata-bench/v2", broken(m, v1), fld)
        for where, ok in (("clients 4 round 2", True), ("clients 8 warmup 1", True), ("chat run 9", True), ("chat warmup 1", True),
                          ("clients round 2", False), ("clients 4 round", False), ("chat run", False), ("clients 4 turn 2", False)):
            f = copy.deepcopy(k)
            f["validity"].update(status="failed", failure={"kind": "crash", "where": where})
            self.assertEqual("failure.where is not one of the protocol's call names" not in validate.problems(f), ok, where)


class SuiteTests(V2Run):
    def suite(self, name, label=None, **kw):
        return self.go(("medium",), label=label or name, protocol=core.PROTOCOL_V2, suite=name, **kw)

    def test_quick_standard_and_full_run_their_own_blocks(self):
        for name, blocks, requests in (("quick", ["first-token", "chat"], 10 + 4 + 4),
                                       ("standard", ["first-token", "chat", "agent"], 10 + 4 * 3 + 20),
                                       ("full", ["first-token", "chat", "agent", "clients"], 10 + 4 * 3 + 20 + 42 + 90)):         # full adds the clients block and the 90 short gprobe calls
            Fake.reset()
            self.assertEqual(self.suite(name), 0, name)
            d = self.doc(name)
            self.assertEqual((d["protocol"]["suite"], d["protocol"]["blocks"]), (name, blocks), name)
            self.assertEqual(len(Fake.st["calls"]), requests, name)
            self.assertEqual(sorted(k for k in d["protocol"] if k in schema.BLOCK_PROTOCOLS), sorted(b for b in blocks if b in schema.BLOCK_PROTOCOLS))
            self.assertEqual(sorted(d["sizes"]), sorted(schema.PROFILE_SIZES[name]), name)
            self.assertEqual(validate.problems(d), [], name)
            self.assertEqual(validate.gate(d), [], name)                               # the profile is exactly what the suite measures

    def test_blocks_the_server_cannot_run_are_skipped_but_the_suite_keeps_its_name(self):
        Fake.st["n_ctx"], Fake.st["slots"] = 8192, 1
        self.assertEqual(self.suite("full", cfg={**CFG, "args": ["8192" if a == "32768" else a for a in CFG["args"]]}), 0)
        d = self.doc("full")
        self.assertEqual(d["protocol"]["blocks"], ["first-token", "chat", "agent", "clients"])
        self.assertEqual(d["protocol"]["skipped"], [{"block": "agent", "reason": "context_too_small"}, {"block": "clients", "reason": "no_parallel_slots"}])
        self.assertTrue("agent" not in d and "clients" not in d)
        self.assertEqual(validate.problems(d), [])

    def test_a_suite_is_for_v2_and_must_be_known(self):
        with self.assertRaises(SystemExit):
            self.go(("medium",), label="x", suite="quick")
        with self.assertRaises(SystemExit):
            self.suite("everything", label="x")
        self.assertFalse(list(self.tmp.glob("x*")))

    def test_the_cli_picks_the_standard_suite_for_v2_and_takes_no_sizes(self):
        cfg = self.tmp / "c.json"
        cfg.write_text(json.dumps(CFG))
        base = ["run", "--base", self.base, "--config", str(cfg), "--origin", "setup-default", "--out", str(self.tmp)]
        self.assertEqual(main(base + ["--label", "s", "--protocol", "standard-v2"]), 0)
        self.assertEqual((self.doc("s")["protocol"]["suite"], self.doc("s")["protocol"]["blocks"]), ("standard", ["first-token", "chat", "agent"]))
        self.assertEqual(main(base + ["--label", "q", "--protocol", "standard-v2", "--suite", "quick"]), 0)
        self.assertEqual(self.doc("q")["protocol"]["suite"], "quick")
        self.assertEqual(main(base + ["--label", "v"]), 0)                                             # v1 is its fixed set
        self.assertEqual(self.doc("v")["schema"], "strata-bench/v1")
        for extra in (["--protocol", "standard-v2", "--sizes", "medium"], ["--sizes", "medium"], ["--suite", "quick"], ["--protocol", "standard-v2", "--suite", "huge"]):
            with self.assertRaises(SystemExit):
                main(base + ["--label", "z"] + extra)
        self.assertFalse((self.tmp / "z.result.json").exists())

    def test_the_request_estimate_is_logged_before_anything_is_measured(self):
        lines = []
        core.run(self.base, CFG, "setup-default", ["medium"], "e", self.tmp, protocol=core.PROTOCOL_V2, suite="quick", log=lines.append)
        self.assertTrue(lines[0].startswith("e: suite quick: chat, medium, long - about 18 requests"), lines[0])
        self.assertEqual(core.planned_requests(["probe", "medium"], 1, 3), 34)
        self.assertEqual([core.planned_requests([x], 1, 3) for x in ("gprobe", "chat", "agent", "clients")], [90, 10, 20, 42])

    def test_validator_ties_a_suite_name_to_its_blocks(self):
        self.suite("quick")
        self.suite("full", label="f")
        q, f = self.doc("quick"), self.doc("f")

        def broken(base, fn):
            d = copy.deepcopy(base)
            fn(d)
            return validate.problems(d)
        self.assertIn("protocol.suite quick lists the blocks first-token, chat", broken(q, lambda d: d["protocol"].update(blocks=["first-token", "chat", "agent"])))
        self.assertIn("protocol.suite quick lists the blocks first-token, chat", broken(q, lambda d: d["protocol"].update(blocks=["chat", "first-token"])))
        self.assertIn("protocol.suite full lists the blocks first-token, chat, agent, clients", broken(f, lambda d: d["protocol"].update(suite="full", blocks=["first-token", "chat"])))
        self.assertEqual([p for p in broken(q, lambda d: d["protocol"].update(suite="custom")) if "suite" in p], [])        # custom may list any blocks it ran
        self.assertEqual([p for p in broken(q, lambda d: d["protocol"].update(suite=["quick"])) if "lists the blocks" in p], [])


class ReviewTests(V2Run):
    """What the blind review of phases 1 to 4 found, each as a test that fails without its fix."""

    def v2c(self, sizes=("medium",), label="v", **kw):
        return self.go(sizes, label=label, **V2, **kw)

    # --- the SSE reader
    def test_a_connection_that_ends_inside_an_event_is_a_crash_not_a_client_error(self):
        self.sse(truncate_after=1)
        self.assertEqual(self.v2c(), 3)
        d = self.doc("v")
        self.assertEqual(d["validity"]["failure"], {"kind": "crash", "where": "medium warmup 1", "http_status": None})
        self.assertNotIn("SA", json.dumps(d["validity"]))                              # the cut text is never kept
        self.assertEqual(validate.problems(d), [])

    def test_an_event_may_span_several_data_lines_and_a_complete_bad_event_is_incomplete(self):
        self.sse(multiline=True)
        self.assertEqual(self.v2c(), 0)
        self.assertTrue(all(r["stream"]["pieces"] == 4 for r in self.doc("v")["runs"]))      # the split event counted as one piece, plus the three normal ones
        Fake.reset()
        self.sse(bad_event=True)
        self.assertEqual(self.v2c(label="w"), 3)
        d = self.doc("w")
        self.assertEqual(d["validity"]["status"], "incomplete")
        self.assertIn("not JSON", d["validity"]["error"])

    def test_a_200_answer_that_is_not_a_stream_is_incomplete_not_a_crash(self):
        self.sse(plain_json=True)
        self.assertEqual(self.v2c(), 3)
        d = self.doc("v")
        self.assertEqual(d["validity"]["status"], "incomplete")
        self.assertIn("did not stream", d["validity"]["error"])

    def test_a_stream_cut_before_its_timings_is_a_crash_but_a_complete_one_without_them_is_not(self):
        self.sse(no_timings=True, no_done=True)
        self.assertEqual(self.v2c(), 3)
        self.assertEqual(self.doc("v")["validity"]["failure"]["kind"], "crash")
        Fake.reset()
        self.sse(no_timings=True)                                                      # [DONE] arrived: the server just sent no timings
        self.assertEqual(self.v2c(label="w"), 3)
        self.assertEqual(self.doc("w")["validity"]["status"], "incomplete")

    def test_the_servers_own_words_do_not_reach_the_result_through_the_finish_reason(self):
        Fake.st["finish"] = "stop <img src=x onerror=alert(1)> /home/someone/secret"
        self.sse()
        self.assertEqual(self.v2c(), 0)
        d = self.doc("v")
        self.assertEqual({r["finish_reason"] for r in d["runs"]}, {"other"})
        self.assertNotIn("someone", json.dumps(d))
        self.assertEqual(validate.problems(d), [])
        Fake.reset()
        self.assertEqual(self.go(("medium",), label="one"), 0)                         # v1 goes through the same closing
        Fake.st["finish"] = "free <b>text</b>"
        self.assertEqual(self.go(("medium",), label="two"), 0)
        self.assertEqual({r["finish_reason"] for r in self.doc("two")["runs"]}, {"other"})
        for good in ("stop", "length", "tool_calls"):
            self.assertEqual(core.closed_finish(good), good)
        self.assertIsNone(core.closed_finish(None))

    def test_a_number_that_is_a_boolean_or_a_string_is_not_a_timing(self):
        for bad in (True, "100", None, [1]):
            r = {"timings": {"prompt_n": bad, "prompt_ms": 5, "prompt_per_second": 5, "predicted_n": 1, "predicted_per_second": 1}}
            with self.assertRaises(core.MeasureError, msg=repr(bad)):
                core.require(r, ("prompt_n", "prompt_ms", "prompt_per_second", "predicted_n", "predicted_per_second"), "x")
        self.assertEqual(core.prune({"prompt_n": "100", "prompt_ms": True, "predicted_n": 3}, schema.TIMINGS), {"prompt_n": None, "prompt_ms": None, "predicted_n": 3})

    # --- the clients block
    def test_a_failure_of_one_client_is_never_hidden_by_another_clients_missing_timings_or_error(self):
        combos = [("drop", "notimings"), ("drop", "http400"), ("garbage", "notimings"), ("drop", "garbage")]
        for a, b in combos:
            for first, second in itertools.permutations((a, b)):
                Fake.reset()
                Fake.st["client_modes"] = {"1": first, "2": second}
                self.assertEqual(self.go(("clients",), label=f"{first}-{second}", **V2), 3, (first, second))
                d = self.doc(f"{first}-{second}")
                self.assertEqual(d["validity"]["status"], "failed", (first, second))
                kinds = {"drop": "crash", "garbage": "garbage"}
                want = min((kinds[m] for m in (first, second) if m in kinds), key=schema_kind_order)
                self.assertEqual(d["validity"]["failure"]["kind"], want, (first, second))
                self.assertEqual(d["validity"]["failure"]["where"], "clients 2 warmup 1")
                self.assertEqual(validate.problems(d), [])
        Fake.reset()
        Fake.st["client_modes"] = {"1": "notimings"}                                   # only missing timings: not a health failure
        self.assertEqual(self.go(("clients",), label="only", **V2), 3)
        self.assertEqual(self.doc("only")["validity"]["status"], "incomplete")

    def canned(self, tokens=10, delay=0.03, busy_rounds=(), busy_kinds=("run",)):
        """A call_stream stand-in: no network, a fixed answer after `delay`, a 429 for the measured rounds listed."""
        def fake(base, messages, max_tokens, extra=None, sink=None):
            first = messages[0]["content"].split("\n", 1)[0]                          # [strata-bench clients <n> <kind> <round> <k>]
            kind, rnd = first.split()[3], int(first.split()[4])
            if kind in busy_kinds and (rnd in busy_rounds or kind == "warmup" and busy_rounds == ("all",)):
                raise urllib.error.HTTPError("http://x", 429, "busy", {}, None)
            time.sleep(delay)
            return {"request_sha256": core.sha256(first.encode()), "wall_ms": delay * 1000, "timings": {"prompt_n": 100, "cache_n": 0, "prompt_ms": 5.0, "prompt_per_second": 1.0,
                    "predicted_n": tokens, "predicted_per_second": 1.0}, "usage": {}, "tokens": {"prompt": 100, "reused": 0, "generated": tokens},
                    "reasoning_chars": 0, "content_chars": 4, "finish_reason": "stop", "output_check": "ok", "output_sha256": "ab" * 32,
                    "stream": {"ttft_ms": delay * 1000, "total_ms": delay * 1000, "pieces": 1, "gap_ms": None}}
        return fake

    def run_clients(self, fake, main_delay=0.0):
        class SlowMain(threading.Barrier):
            def wait(self, timeout=None):
                r = super().wait(timeout)
                if threading.current_thread() is threading.main_thread():
                    time.sleep(main_delay)                                              # the main thread is slow to wake after the release
                return r
        res = core.Result(self.tmp / "c.result.json", {"runs": [], "validity": {"warnings": []}})
        with mock.patch.object(core, "call_stream", fake), mock.patch.object(core.threading, "Barrier", SlowMain):
            core.measure_clients("http://x", res, lambda *a: None)
        return res.doc

    def test_the_round_ends_at_the_last_answer_and_starts_before_the_release_whatever_the_main_thread_does(self):
        quick = self.run_clients(self.canned(tokens=10, delay=0.03), main_delay=0.0)
        slow = self.run_clients(self.canned(tokens=10, delay=0.03), main_delay=0.15)       # the main thread wakes long after every client has finished
        for a, b in zip(quick["clients"], slow["clients"]):
            top, bottom = a["n"] * 10 / 0.03, a["n"] * 10 / 0.12                           # 10 tokens per client, answered in 30 ms plus scheduling slack
            for row in (a, b):
                self.assertLessEqual(row["total_tps"], top)
                self.assertGreaterEqual(row["total_tps"], bottom)
            self.assertGreater(b["total_tps"], 0.5 * a["total_tps"])                       # the main thread's sleep is not in the denominator

    def test_a_round_in_which_nothing_was_generated_counts_as_zero_in_the_median(self):
        both = self.run_clients(self.canned(tokens=10, delay=0.05))
        half = self.run_clients(self.canned(tokens=10, delay=0.05, busy_rounds=(2,)))
        for a, b in zip(both["clients"], half["clients"]):
            self.assertEqual((b["requests"], b["errors"]), (2 * a["n"], a["n"]))
            self.assertLess(b["total_tps"], 0.75 * a["total_tps"])                      # [x, 0] has median x/2, not x
            self.assertGreater(b["total_tps"], 0.3 * a["total_tps"])

    def test_clients_that_read_a_cached_prompt_are_warned_about(self):
        Fake.st["reused"] = 500
        self.assertEqual(self.go(("clients",), label="r", **V2), 0)
        d = self.doc("r")
        self.assertEqual(d["validity"]["status"], "warnings")
        self.assertEqual([w for w in d["validity"]["warnings"] if "reused" in w], [f"clients {n}: 500 prompt tokens reused, the prompt was not read fresh" for n in (2, 4, 8)])

    def test_two_clients_failing_the_same_way_through_different_channels_is_still_a_failure_not_a_crash_of_the_runner(self):
        def fail_with(by_client):
            def fake(base, messages, max_tokens, extra=None, sink=None):
                k = int(messages[0]["content"].split("\n", 1)[0].rstrip("]").split()[-1])
                time.sleep(0.01)
                if k in by_client:
                    raise by_client[k]()
                return self.canned()(base, messages, max_tokens, extra, sink)
            return fake
        sse = lambda kind: (lambda: core.MeasureFailure(kind))                          # an error event in the stream: no HTTP status
        http = lambda body: (lambda: urllib.error.HTTPError("http://x", 503, "unavailable", {}, io.BytesIO(body)))
        cases = [("server_error", 503, {1: sse("server_error"), 2: http(b"engine is starting")}), ("server_error", 503, {1: http(b"engine is starting"), 2: sse("server_error")}),
                 ("oom", 503, {1: sse("oom"), 2: http(b"CUDA out of memory")}), ("oom", 503, {1: http(b"CUDA out of memory"), 2: sse("oom")}),
                 ("crash", None, {1: sse("server_error"), 2: lambda: core.MeasureFailure("crash")}), ("oom", 503, {1: http(b"engine is starting"), 2: http(b"CUDA out of memory")})]
        for kind, status, by_client in cases:
            res = core.Result(self.tmp / "m.result.json", {"runs": [], "validity": {"warnings": []}})
            with mock.patch.object(core, "call_stream", fail_with(by_client)), self.assertRaises(core.MeasureFailure) as cm:
                core.measure_clients("http://x", res, lambda *a: None)
            self.assertEqual((cm.exception.kind, cm.exception.http_status, res.where), (kind, status, "clients 2 warmup 1"), by_client)

    def test_a_connection_that_ends_after_only_keep_alives_or_inside_the_first_event_is_a_crash(self):
        for opts in ({"keepalive": True, "drop_before_first": True}, {"truncate_after": 0}):
            Fake.reset()
            self.sse(**opts)
            self.assertEqual(self.v2c(label="d"), 3, opts)
            self.assertEqual(self.doc("d")["validity"]["failure"], {"kind": "crash", "where": "medium warmup 1", "http_status": None}, opts)
            (self.tmp / "d.result.json").unlink()
        Fake.reset()
        self.sse(plain_json=True)                                                       # a 200 answer that is not an event stream stays incomplete
        self.assertEqual(self.v2c(label="d"), 3)
        self.assertEqual(self.doc("d")["validity"]["status"], "incomplete")

    def test_a_refusal_during_the_warm_up_alone_still_makes_the_result_a_warning(self):
        res = core.Result(self.tmp / "w.result.json", {"runs": [], "validity": {"warnings": []}})
        with mock.patch.object(core, "call_stream", self.canned(busy_rounds=("all",), busy_kinds=("warmup",))):
            core.measure_clients("http://x", res, lambda *a: None)
        d = res.doc
        self.assertEqual([(c["requests"], c["errors"]) for c in d["clients"]], [(4, 0), (8, 0), (16, 0)])      # the measured counters are untouched
        self.assertEqual(len([w for w in d["validity"]["warnings"] if "warm-up request(s) turned away" in w]), 3)
        self.assertEqual(len([r for r in d["runs"] if r.get("error_status") == 429 and r["kind"] == "warmup"]), 2 + 4 + 8)

    def test_nan_and_infinity_are_not_json_and_never_reach_a_result_or_the_dashboard_data(self):
        self.assertEqual(self.go(("medium",), label="n"), 0)
        text = (self.tmp / "n.result.json").read_text()
        for literal in ("NaN", "Infinity", "-Infinity"):
            bad = self.tmp / "bad.result.json"
            bad.write_text(text.replace('"os": "', f'"vram_gb": {literal}, "os": "', 1) if '"os": "' in text else text.replace('"machine": {', f'"machine": {{"vram_gb": {literal}, ', 1))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(["validate", str(bad)]), 1, literal)
            self.assertIn("not valid JSON", out.getvalue(), literal)                      # refused when read, before any check of its content
            with self.assertRaises(ValueError):
                schema.strict_loads(bad.read_text())
        doc = json.loads(text)
        doc["machine"] = {"gpus": [{"name": "x", "vram_gb": float("nan")}], "ram_gb": float("inf")}
        self.assertEqual(len([p for p in validate.problems(doc) if "not a scalar" in p]), 2)
        self.assertEqual(schema.prune({"a": float("nan"), "b": 1}, {"a": schema.SCALAR, "b": schema.SCALAR}), {"a": None, "b": 1})
        import sys
        sys.path.insert(0, str(core.PROMPTS.parent.parent / "tools"))
        import build_site
        with self.assertRaises(ValueError):
            build_site.dump({"x": float("nan")})


def schema_kind_order(kind):
    return schema.FAILURE_KINDS.index(kind)


class ScoutTests(V2Run):
    """What the scout found after the second review round."""

    def test_the_runner_keeps_no_credential_or_address_in_the_recorded_arguments(self):
        cfg = {**CFG, "args": CFG["args"] + ["--api-key", "opaque-demo-secret", "--host", "gpu-box.internal", "--token=abc", "--endpoint=http://gpu-box.internal:8080", "--bind", "10.1.2.3"]}
        cfg["args"] = [a if a != "10.1.2.3" else "gpu-box.internal" for a in cfg["args"]]
        self.assertEqual(self.go(("medium",), label="s", cfg=cfg), 0)
        d = self.doc("s")
        text = json.dumps(d)
        for secret in ("opaque-demo-secret", "gpu-box.internal", "abc"):
            self.assertNotIn(secret, text.replace("abcdef", ""), secret)
        args = d["config"]["args"]
        self.assertEqual([args[args.index(f)+1] for f in ("--api-key", "--host", "--bind")], ["<redacted>", "<host>", "<host>"])
        self.assertIn("--token=<redacted>", args)
        self.assertIn("--endpoint=<host>", args)
        self.assertEqual(args[args.index("--max-context") + 1], "32768")                # the rest of the configuration is untouched
        self.assertEqual(validate.problems(d), [])
        d["config"]["args"][args.index("--api-key") + 1] = "opaque-demo-secret"           # an edited file that still carries one is refused
        self.assertIn(f"config.args[{args.index('--api-key')}] carries a credential or an address", validate.problems(d))

    def test_a_leak_is_judged_on_the_decoded_text_whatever_comes_before_it(self):
        self.assertEqual(self.go(("medium",), label="s"), 0)
        d = self.doc("s")
        for prefix in ("\n", "\t", "\x00", "\r", "x", "\u00e9"):
            for text, what in (("sk-abcdefgh12345", "a token"), ("/home/alice/secret", "an absolute path"), ("10.1.2.3", "a private network address")):
                e = copy.deepcopy(d)
                e["label"] = prefix + text
                if prefix in ("x", "\u00e9"):                                             # a letter in front of a token or an address is part of a word: not a leak
                    continue
                self.assertTrue(any(f"contains {what}" in p for p in validate.problems(e)), (prefix, text))
        e = copy.deepcopy(d)
        e.update({"2": "/alpha/secret/", "1": "/beta/secret/"})
        self.assertEqual([p for p in validate.problems(e) if p.startswith("contains an absolute path")], ["contains an absolute path: '/beta/'"])

    def test_a_result_nested_too_deep_is_refused_without_recursion(self):
        self.assertEqual(self.go(("medium",), label="s"), 0)
        d = self.doc("s")
        node = leaf = {}
        for _ in range(5000):
            leaf["x"] = {}
            leaf = leaf["x"]
        d["unexpected"] = node
        self.assertEqual(validate.problems(d), [f"nested more than {schema.MAX_DEPTH} levels deep"])
        try:                                                                              # reading it either works (newer Pythons) or is a ValueError (older ones hit their stack): never anything else
            deep = schema.strict_loads('{"a":' * 5000 + "1" + "}" * 5000)
        except ValueError:
            pass
        else:
            self.assertEqual(validate.problems(deep), [f"nested more than {schema.MAX_DEPTH} levels deep"])

    def test_numbers_outside_what_javascript_reads_are_refused_not_a_crash(self):
        self.assertEqual(self.go(("medium",), label="s"), 0)
        d = self.doc("s")
        for where in (lambda e: e.update(n_ctx=10 ** 400), lambda e: e["validity"].update(status=10 ** 400), lambda e: e["protocol"].update(id=10 ** 400),
                      lambda e: e["runs"][0].update(index=10 ** 400)):
            e = copy.deepcopy(d)
            where(e)
            self.assertTrue(validate.problems(e))                                       # a list of problems, never OverflowError
        self.assertEqual(validate.shown(10 ** 400), "(value not shown)")
        self.assertIsNone(validate.ident(10 ** 400, 1))
        self.assertIsNone(validate.ident(2 ** 60))                                       # JavaScript cannot name it exactly
        self.assertEqual(validate.ident(3, 4.0), (3, 4))

    def test_a_result_must_agree_with_itself_about_its_health(self):
        Fake.st["busy_client"], Fake.st["busy_status"] = "2", 429
        self.assertEqual(self.go(("clients",), label="b", **V2), 0)
        b = self.doc("b")
        self.assertEqual(b["validity"]["status"], "warnings")
        e = copy.deepcopy(b)
        e["validity"].update(status="ok", warnings=[])
        self.assertIn("a result with refused requests needs status warnings", validate.problems(e))
        Fake.reset()
        self.assertEqual(self.go(("medium",), label="m", **V2), 0)
        m = self.doc("m")
        for fn, msg in ((lambda e: [r for r in e["runs"] if r["kind"] == "run"][0].update(output_check="garbage"), "a run with degenerate output needs a failed result"),
                        (lambda e: e["validity"].update(warnings=["x"]), "status ok cannot carry warnings"),
                        (lambda e: e["validity"].update(status="warnings"), "status warnings needs at least one warning"),
                        (lambda e: e["validity"].update(error="the server reported an error"), "validity.error is only allowed on a failed result")):
            e = copy.deepcopy(m)
            fn(e)
            self.assertIn(msg, validate.problems(e))
        e = copy.deepcopy(m)
        e["validity"].update(status="failed", failure={"kind": "crash", "where": "medium run 1"})
        [r for r in e["runs"] if r["kind"] == "run"][0].update(output_check="garbage")
        self.assertEqual(validate.problems(e), [])                                       # a failed result may carry the garbage run that failed it

    def test_declared_text_is_limited_a_private_hostname_and_a_long_string_are_refused(self):
        self.assertEqual(self.go(("medium",), label="s"), 0)
        d = self.doc("s")
        for text in ("gpu-box.internal", "rig.LOCAL", "see gpu-box.lan", "a.b.corp", "nas.home", "x.intranet", "pc.localdomain", "a.private"):
            e = copy.deepcopy(d)
            e["label"] = text
            self.assertTrue(any("contains a private hostname" in p for p in validate.problems(e)), text)
        for text in ("my.localhost", "x.homepage.com", "Strata 0.1.43.1", "RTX 5090 + RTX 3090", "a" * schema.MAX_STRING, "\U0001F600" * schema.MAX_STRING):
            e = copy.deepcopy(d)
            e["label"] = text
            self.assertEqual(validate.problems(e), [], text[:30])
        e = copy.deepcopy(d)
        e["label"] = "a" * (schema.MAX_STRING + 1)
        self.assertEqual(validate.problems(e), [f"contains a string longer than {schema.MAX_STRING} characters"])
        e = copy.deepcopy(d)
        e["config"]["env"]["K" * (schema.MAX_STRING + 1)] = "1"                       # a key counts too
        self.assertIn(f"contains a string longer than {schema.MAX_STRING} characters", validate.problems(e))


class CoverageTests(V2Run):
    """The gate asks for the content a block promises, not only for its name."""

    def results(self):
        out = {}
        for name, suite in (("quick", "quick"), ("full", "full")):
            Fake.reset()
            self.go(("medium",), label=name, protocol=core.PROTOCOL_V2, suite=suite)
            out[name] = self.doc(name)
        return out

    def test_a_block_without_its_content_is_refused(self):
        r = self.results()
        full, quick = r["full"], r["quick"]

        def broken(base, fn):
            d = copy.deepcopy(base)
            fn(d)
            return validate.problems(d)
        self.assertEqual(validate.problems(full), [])
        self.assertIn("a result with the first-token block needs ttft_ms in a text size and its streamed runs",
                      broken(quick, lambda d: d.update(runs=[], sizes={"chat": {"ttft_ms": {"median": 1}}})))
        self.assertIn("a result with the first-token block needs ttft_ms in a text size and its streamed runs",
                      broken(quick, lambda d: [d["sizes"][z].pop("ttft_ms") for z in ("medium", "long")]))
        self.assertIn("a result with the first-token block needs ttft_ms in a text size and its streamed runs",
                      broken(quick, lambda d: [r_.pop("stream") for r_ in d["runs"] if r_["size"] in ("medium", "long")]))
        self.assertIn("a result with the chat block needs sizes.chat", broken(full, lambda d: d["sizes"]["chat"].pop("ttft_ms")))
        self.assertIn("a result with the chat block needs sizes.chat", broken(full, lambda d: d["runs"].remove([r_ for r_ in d["runs"] if r_["size"] == "chat" and r_["kind"] == "run"][0])))
        self.assertIn("a result with the agent block needs agent.sessions", broken(full, lambda d: d["agent"]["sessions"].pop()))
        self.assertIn("a result with the agent block needs agent.sessions", broken(full, lambda d: d["runs"].remove([r_ for r_ in d["runs"] if r_["size"] == "agent" and r_["kind"] == "run"][0])))
        self.assertIn("a result with the clients block needs clients", broken(full, lambda d: d["clients"].pop()))
        self.assertIn("a result with the clients block needs clients", broken(full, lambda d: d["clients"][1].update(requests=9)))
        self.assertIn("a result with the clients block needs clients", broken(full, lambda d: d["clients"][0].update(n=3)))
        self.assertIn("a result with the clients block needs clients", broken(full, lambda d: d["clients"].reverse()))
        self.assertIn("a result with the clients block needs clients", broken(full, lambda d: d.update(clients=[{}, {}, {}])))
        self.assertIn("a result with the agent block needs agent.sessions", broken(full, lambda d: d.update(runs=[], sizes={"chat": {}}, agent={"sessions": [{}, {}, {}]}, clients=[{}])))
        # partial failures and approved skips keep their meaning
        failed = copy.deepcopy(full)
        failed["validity"].update(status="failed", failure={"kind": "crash", "where": "clients 4 round 1"})
        failed["runs"], failed["sizes"], failed["clients"] = failed["runs"][:5], {}, failed["clients"][:1]
        self.assertEqual(validate.problems(failed), [])

    def test_skips_are_the_two_approved_pairs_and_decode_curve_is_not_accepted(self):
        full = self.results()["full"]

        def broken(fn):
            d = copy.deepcopy(full)
            fn(d)
            return validate.problems(d)
        self.assertTrue(any("protocol.skipped must list" in p for p in broken(lambda d: d["protocol"].update(skipped=[{"block": "chat", "reason": "no_parallel_slots"}]))))
        self.assertTrue(any("protocol.skipped must list" in p for p in broken(lambda d: d["protocol"].update(skipped=[{"block": "agent", "reason": "no_parallel_slots"}]))))
        self.assertTrue(any("protocol.skipped must list" in p for p in broken(lambda d: d["protocol"].update(skipped=[{"block": "clients", "reason": "no_stream_timings"}]))))
        self.assertIn("protocol.blocks must be a list of " + ", ".join(schema.BLOCKS), broken(lambda d: d["protocol"].update(blocks=d["protocol"]["blocks"] + ["decode-curve"])))
        self.assertNotIn("decode-curve", schema.BLOCKS)


if __name__ == "__main__":
    unittest.main()
