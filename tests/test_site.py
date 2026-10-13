"""tools/build_site.py: the page's data comes only from results that pass the public-export gate, with whitelisted fields, deterministically."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import build_site  # noqa: E402
from strata_bench import core, validate  # noqa: E402
from tests import test_core as TC  # noqa: E402


class Site(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "results").mkdir()
        # real results from the core against the test server (never published): one ok, one failed
        TC.Fake.reset()
        self.srv = TC.Threaded(("127.0.0.1", 0), TC.Fake)
        TC.threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.srv.shutdown(), self.srv.server_close()))
        self.base = f"http://127.0.0.1:{self.srv.server_port}"

    def make(self, label, mode=None, identity=None):
        TC.Fake.st["mode"], TC.Fake.st["mode_after"] = mode, 1
        core.run(self.base, TC.CFG, "setup-default", ["medium"], label, self.tmp / "results", identity, log=lambda *a: None)
        TC.Fake.st["mode"] = None
        return self.tmp / "results" / f"{label}.result.json"

    IDENT = {"machine": {"gpus": [{"name": "RTX 5090", "vram_gb": 32}], "cpu": "Ryzen 9 5950X", "ram_gb": 96, "os": "Windows 11"},
             "engine": {"version": "0.1.40.2", "commit": "abc1234"}, "model": {"family": "Swift", "quant": "IQ3_XXS"}}

    def test_a_valid_result_becomes_a_whitelisted_row(self):
        self.make("good", identity=self.IDENT)
        data = build_site.build(self.tmp / "results")
        self.assertEqual((data["count"], data["protocols"]), (1, ["standard-v1"]))
        r = data["results"][0]
        self.assertEqual((r["status"], r["gpu"], r["engine"], r["model"], r["quant"]), ("ok", "RTX 5090", "0.1.40.2", "Swift", "IQ3_XXS"))
        self.assertEqual(r["sizes"]["medium"]["decode_tps"], 200.0)
        self.assertEqual((r["by"], r["machine_id"]), (None, None))                   # optional fields stay empty unless declared
        self.assertEqual(r["id"], "results/good.result.json")
        self.assertRegex(r["config_id"], r"^[0-9a-f]{16}$")
        self.assertNotIn("runs", r)                                                  # per-request data stays in the result file, not on the page

    def test_a_v2_result_carries_first_token_agent_and_clients_numbers_and_v1_rows_stay_empty(self):
        self.make("old", identity=self.IDENT)
        core.run(self.base, TC.CFG, "setup-default", [], "new", self.tmp / "results", self.IDENT, protocol="standard-v2", suite="full",
                 host_relation="lan", log=lambda *a: None)
        data = build_site.build(self.tmp / "results")
        self.assertEqual(data["protocols"], ["standard-v1", "standard-v2"])
        old, new = (next(r for r in data["results"] if r["label"] == n) for n in ("old", "new"))
        self.assertEqual((old["protocol"], old["suite"], old["host_relation"], old["agent"], old["clients"]), ("standard-v1", None, None, None, None))
        self.assertNotIn("ttft_ms", {k for k, v in old["sizes"]["medium"].items() if v is not None})
        self.assertEqual((new["protocol"], new["suite"], new["host_relation"]), ("standard-v2", "full", "lan"))
        self.assertEqual(sorted(new["sizes"]), ["chat", "long", "medium", "xlong"])
        self.assertIsNotNone(new["sizes"]["medium"]["ttft_ms"])
        self.assertEqual(sorted(new["agent"]), ["hit_pct", "reread_tokens", "ttft_last_turn_ms", "ttft_turn1_ms"])
        self.assertGreater(new["agent"]["hit_pct"], 90)
        self.assertEqual([c["n"] for c in new["clients"]], [2, 4, 8])
        self.assertEqual(sorted(new["clients"][0]), ["errors", "n", "total_tps", "ttft_p95_ms"])

    def test_the_same_gpu_declared_with_and_without_the_vendor_name_is_one_gpu_on_the_page(self):
        for name, shown in (("NVIDIA GeForce RTX 5090", "RTX 5090"), ("RTX 5090", "RTX 5090"), ("GeForce RTX 3090", "RTX 3090"), ("NVIDIA RTX 4000 Ada Generation", "RTX 4000 Ada Generation"),
                            ("AMD Radeon RX 7900 XTX", "Radeon RX 7900 XTX"), ("nvidia geforce rtx 4090", "rtx 4090"), ("Tesla P100", "Tesla P100"), ("NVIDIA", "NVIDIA"), (None, None)):
            self.assertEqual(build_site.gpu_name(name), shown, name)
        ident = json.loads(json.dumps(self.IDENT))
        ident["machine"]["gpus"][0]["name"] = "NVIDIA GeForce RTX 5090"
        self.make("long-name", identity=ident)
        self.make("short-name", identity=self.IDENT)
        rows = build_site.build(self.tmp / "results")["results"]
        self.assertEqual({r["gpu"] for r in rows}, {"RTX 5090"})
        self.assertIn("NVIDIA GeForce RTX 5090", (self.tmp / "results" / "long-name.result.json").read_text())      # the file keeps what was declared

    def test_a_failed_result_is_published_for_the_health_view(self):
        self.make("boom", mode="crash", identity=self.IDENT)
        r = build_site.build(self.tmp / "results")["results"][0]
        self.assertEqual((r["status"], r["failure"]["kind"]), ("failed", "crash"))

    def test_a_result_that_fails_the_gate_stops_the_build(self):
        p = self.make("leaky", identity=self.IDENT)
        d = json.loads(p.read_text())
        d["config"]["args"] = ["--pack", "E:\\private\\pack"]
        p.write_text(json.dumps(d))
        with self.assertRaises(SystemExit) as cm:
            build_site.build(self.tmp / "results")
        self.assertIn("leaky.result.json", str(cm.exception))
        self.assertIn("a Windows path", str(cm.exception))

    def test_an_incomplete_result_and_bad_json_stop_the_build(self):
        TC.Fake.st["mode"], TC.Fake.st["mode_after"] = "bad_request", 1
        core.run(self.base, TC.CFG, "setup-default", ["medium"], "inc", self.tmp / "results", None, log=lambda *a: None)
        with self.assertRaises(SystemExit):
            build_site.build(self.tmp / "results")
        (self.tmp / "results" / "inc.result.json").unlink()
        (self.tmp / "results" / "junk.result.json").write_text("{nope")
        with self.assertRaises(SystemExit) as cm:
            build_site.build(self.tmp / "results")
        self.assertIn("not JSON", str(cm.exception))

    def test_unlisted_fields_never_reach_the_page(self):
        p = self.make("extra", identity=self.IDENT)
        d = json.loads(p.read_text())
        d["machine"]["hostname"] = "my-box"
        p.write_text(json.dumps(d))
        with self.assertRaises(SystemExit):                                          # not in the public schema: the gate refuses it
            build_site.build(self.tmp / "results")

    def test_the_output_is_deterministic_and_sorted_newest_first(self):
        a, b = self.make("a", identity=self.IDENT), self.make("b", identity=self.IDENT)
        for p, date in ((a, "2026-01-01T00:00:00+00:00"), (b, "2026-02-01T00:00:00+00:00")):
            d = json.loads(p.read_text())
            d["submission"]["date"] = date
            p.write_text(json.dumps(d))
        t1, t2 = build_site.dump(build_site.build(self.tmp / "results")), build_site.dump(build_site.build(self.tmp / "results"))
        self.assertEqual(t1, t2)
        self.assertEqual([r["label"] for r in json.loads(t1)["results"]], ["b", "a"])

    def test_check_mode_detects_a_stale_data_file(self):
        self.make("one", identity=self.IDENT)
        out = self.tmp / "data.json"
        args = ["--results", str(self.tmp / "results"), "--out", str(out)]
        self.assertEqual(build_site.main(args + ["--check"]), 1)                    # missing
        self.assertEqual(build_site.main(args), 0)
        self.assertEqual(build_site.main(args + ["--check"]), 0)
        out.write_text(out.read_text().replace('"count": 1', '"count": 2'))
        self.assertEqual(build_site.main(args + ["--check"]), 1)                    # stale

    def test_the_validated_files_are_copied_for_the_detail_page_and_kept_in_sync(self):
        self.make("one", identity=self.IDENT)
        out = self.tmp / "docs" / "data.json"
        out.parent.mkdir()
        args = ["--results", str(self.tmp / "results"), "--out", str(out)]
        copy = self.tmp / "docs" / "results" / "one.result.json"
        self.assertEqual(build_site.main(args), 0)
        self.assertEqual(copy.read_text(), (self.tmp / "results" / "one.result.json").read_text())
        self.assertEqual(build_site.main(args + ["--check"]), 0)
        copy.write_text("{}")                                                        # a stale copy is caught, and the build repairs it
        self.assertEqual(build_site.main(args + ["--check"]), 1)
        self.assertEqual(build_site.main(args), 0)
        self.assertEqual(build_site.main(args + ["--check"]), 0)
        (self.tmp / "docs" / "results" / "gone.result.json").write_text("{}")        # a copy whose source was removed is caught and removed
        self.assertEqual(build_site.main(args + ["--check"]), 1)
        self.assertEqual(build_site.main(args), 0)
        self.assertFalse((self.tmp / "docs" / "results" / "gone.result.json").exists())
        self.assertEqual(build_site.main(args + ["--check"]), 0)

    def test_every_result_row_has_its_copy_in_docs(self):
        d = json.loads((ROOT / "docs" / "data.json").read_text())
        for r in d["results"]:
            self.assertTrue((ROOT / "docs" / r["id"]).exists(), r["id"])             # the page links docs/<id>; Pages publishes only docs/

    def test_the_committed_data_file_is_well_formed(self):
        d = json.loads((ROOT / "docs" / "data.json").read_text())                  # rebuilt by CI after each merge; here only its shape
        self.assertEqual(d["count"], len(d["results"]))
        self.assertEqual(d["protocols"], sorted(set(d["protocols"])))               # which protocols are listed depends on the results published so far
        self.assertTrue(set(d["protocols"]) <= {"standard-v1", "standard-v2"})
        self.assertTrue(set(d["protocols"]) >= {r["protocol"] for r in d["results"]})

    def test_the_empty_state_builds(self):
        data = build_site.build(self.tmp / "results")
        self.assertEqual((data["count"], data["results"]), (0, []))


if __name__ == "__main__":
    unittest.main()
