"""standard-v2, phase 1: streamed requests, time to the first token, the additive v2 schema and its validator."""
import copy
import json
import unittest

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
        self.assertEqual(d["protocol"]["prompt_sha256"], dict(schema.STANDARD_V1_PROMPTS))
        d["protocol"]["prompt_sha256"]["medium.txt"] = "ab" * 32
        self.assertIn("prompt medium.txt is not standard-v1's", validate.problems(d))

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


if __name__ == "__main__":
    unittest.main()
