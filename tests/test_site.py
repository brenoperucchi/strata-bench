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
        self.srv = TC.HTTPServer(("127.0.0.1", 0), TC.Fake)
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
        self.assertEqual((data["count"], data["protocol"]), (1, "standard-v1"))
        r = data["results"][0]
        self.assertEqual((r["status"], r["gpu"], r["engine"], r["model"], r["quant"]), ("ok", "RTX 5090", "0.1.40.2", "Swift", "IQ3_XXS"))
        self.assertEqual(r["sizes"]["medium"]["decode_tps"], 200.0)
        self.assertEqual(r["id"], "results/good.result.json")
        self.assertRegex(r["config_id"], r"^[0-9a-f]{16}$")
        self.assertNotIn("runs", r)                                                  # per-request data stays in the result file, not on the page

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

    def test_the_committed_data_file_is_well_formed(self):
        d = json.loads((ROOT / "docs" / "data.json").read_text())                  # rebuilt by CI after each merge; here only its shape
        self.assertEqual((d["protocol"], d["count"]), ("standard-v1", len(d["results"])))

    def test_the_empty_state_builds(self):
        data = build_site.build(self.tmp / "results")
        self.assertEqual((data["count"], data["results"]), (0, []))


if __name__ == "__main__":
    unittest.main()
