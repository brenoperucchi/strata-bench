"""standard-v2, phase 1: streamed requests, time to the first token, the additive v2 schema and its validator."""
import copy
import json
import shutil
import unittest
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
        self.assertIn("protocol ['standard-v2'] is not public", broken(v2, lambda d: d["protocol"].update(id=["standard-v2"])))
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
                   "--out", str(self.tmp), "--sizes", "medium", "--protocol", "standard-v2", "--host-relation", "same-host"])
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


if __name__ == "__main__":
    unittest.main()
