"""The importer of the Strata community reports and the page that lists them: what is read, what stays empty, and that nothing leaks into the results."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import import_strata_reports as imp  # noqa: E402

NODE = shutil.which("node")

GUIDE = """# Community benchmark results

## Community reports

- [2026-10-08: 2x TITAN RTX 24 GB, Xeon E5-2696 v4, 125 GiB RAM](../bench/results/2026-10-08-community-titan/README.md):
  Strata v0.1.40.3 source build, IQ3_S, 262,144-token context; three runs each.
- [2026-10-04: RX 7900 GRE 16 GB](../bench/results/2026-10-04-community-gre/README.md):
  Coder IQ1_M and Flash-Next.
"""

TITAN = """# 2x TITAN RTX

## Hardware and software

- GPU: 2x NVIDIA TITAN RTX 24 GB
- CPU: Xeon E5-2696 v4
- Strata: v0.1.40.3

## Results

| Prompt tokens | Prompt t/s (3 runs) | Median | Decode t/s (3 runs) | Median | TTFT (3 runs) |
| --- | --- | --- | --- | --- | --- |
| 4,096 | 886.8 / 922.6 / 922.8 | 922.6 | 75.4 / 65.1 / 80.3 | 75.4 | 3.46 / 3.33 / 3.32 s |
| 32,768 | 1647.2 / 1638.8 / 1642.1 | 1642.1 | 63.9 / 83.0 / 80.2 | 80.2 | 15.07 / 15.14 / 15.12 s |
"""

GRE = """# RX 7900 GRE

## Results

| Quant | Prompt tokens | Reused | Prompt tok/s | Decode tok/s | Total seconds |
| --- | ---: | ---: | --- | --- | --- |
| Coder IQ1_M | 4,210 | 0 | 1,224.1 [1,210.4-1,273.1] | 48.5 [46.3-52.4] | 6.1 [5.8-6.3] |
| Flash-Next IQ2_XS | 8,830 | 0 | 1,154.7 [1,152.6-1,159.4] | 65.3 [63.6-68.2] | 9.6 |
"""

NOTABLE = "# Only text\n\nNo table here, only a story about a machine.\n"


def make_source(tmp: Path):
    (tmp / "docs").mkdir(parents=True)
    (tmp / "docs" / "COMMUNITY_BENCHMARKS.md").write_text(GUIDE)
    for name, text in (("2026-10-08-community-titan", TITAN), ("2026-10-04-community-gre", GRE), ("2026-10-02-community-plain", NOTABLE)):
        d = tmp / "bench" / "results" / name
        d.mkdir(parents=True)
        (d / "README.md").write_text(text)
    other = tmp / "bench" / "results" / "2026-09-24-final"            # not a community report: left out
    other.mkdir(parents=True)
    (other / "README.md").write_text(GRE)


class Importer(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        make_source(self.tmp)
        self.data = imp.build(self.tmp)
        self.by = {e["id"]: e for e in self.data["reports"]}

    def test_only_community_folders_are_read_newest_first(self):
        self.assertEqual([e["id"] for e in self.data["reports"]], ["2026-10-08-community-titan", "2026-10-04-community-gre", "2026-10-02-community-plain"])
        self.assertEqual(self.data["count"], 3)

    def test_the_median_column_is_used_and_a_list_of_runs_is_never_taken_for_a_figure(self):
        row = self.by["2026-10-08-community-titan"]["headline_row"]
        self.assertEqual((row["prompt_tps"], row["decode_tps"], row["prompt_tokens"]), (922.6, 75.4, 4096.0))
        self.assertIsNone(row["ttft_s"])                                   # '3.46 / 3.33 / 3.32 s' lists runs and has no median column

    def test_a_label_keeps_its_underscores_and_the_first_number_of_a_cell_with_a_range_is_the_median(self):
        row = self.by["2026-10-04-community-gre"]["headline_row"]
        self.assertEqual((row["label"], row["prompt_tps"], row["decode_tps"]), ("Coder IQ1_M", 1224.1, 48.5))

    def test_what_the_report_does_not_say_stays_empty(self):
        plain = self.by["2026-10-02-community-plain"]
        self.assertEqual((plain["rows"], plain["headline_row"], plain["gpu"], plain["engine"]), ([], None, None, None))

    def test_the_curated_index_gives_the_date_headline_and_summary(self):
        titan = self.by["2026-10-08-community-titan"]
        self.assertEqual(titan["date"], "2026-10-08")
        self.assertIn("TITAN RTX", titan["headline"])
        self.assertIn("262,144", titan["summary"])
        self.assertEqual(titan["context"], 262144)
        self.assertEqual(titan["quant"], "IQ3_S")

    def test_each_entry_says_where_it_came_from(self):
        src = self.by["2026-10-08-community-titan"]["source"]
        self.assertTrue(src["url"].startswith("https://github.com/Niko1221/Strata/tree/"))
        self.assertEqual(len(src["readme_sha256"]), 64)
        self.assertIn("Not comparable", self.data["note"])

    def test_the_importer_writes_nothing_under_results(self):
        before = sorted(p.name for p in (ROOT / "results").glob("*.json"))
        out = self.tmp / "community.json"
        self.assertEqual(imp.main(["--source", str(self.tmp), "--out", str(out)]), 0)
        self.assertTrue(out.exists())
        self.assertEqual(sorted(p.name for p in (ROOT / "results").glob("*.json")), before)

    def test_a_source_without_results_is_refused(self):
        self.assertEqual(imp.main(["--source", str(self.tmp / "docs"), "--out", str(self.tmp / "x.json")]), 2)


@unittest.skipUnless(NODE, "node is not installed")
class Page(unittest.TestCase):
    def render(self, data, query=""):
        js = "const C = require('./docs/community.js'); console.log(JSON.stringify(C.render(%s, %s)));" % (json.dumps(data), json.dumps(query))
        r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def data(self):
        return {"source": {"repository": "https://github.com/Niko1221/Strata", "commit": "477ac13cbe8e"}, "reports": [
            {"id": "a", "date": "2026-10-08", "headline": "2x TITAN RTX 24 GB", "quant": "IQ3_S", "context": 262144, "summary": "s",
             "headline_row": {"prompt_tps": 922.6, "decode_tps": 75.4, "prompt_tokens": 4096, "label": "4,096"}, "source": {"url": "https://github.com/Niko1221/Strata/tree/477ac13c/bench/results/a"}},
            {"id": "b", "date": "2026-10-04", "headline": "RX 7900 GRE", "quant": None, "context": None, "headline_row": None, "source": {"url": "https://github.com/Niko1221/Strata/tree/477ac13c/bench/results/b"}}]}

    def test_the_page_says_the_reports_are_not_comparable_and_lists_them(self):
        html = self.render(self.data())
        self.assertIn("Not comparable with the Results tab", html)
        self.assertIn("2x TITAN RTX 24 GB", html)
        self.assertIn("923", html)                                             # 922.6 tok/s, shown rounded
        self.assertIn("477ac13c", html)

    def test_the_filter_keeps_the_rows_that_match_every_word(self):
        self.assertIn("RX 7900 GRE", self.render(self.data(), "gre"))
        self.assertNotIn("TITAN", self.render(self.data(), "gre"))
        self.assertIn("No report matches", self.render(self.data(), "gre titan"))

    def test_nothing_from_the_file_reaches_the_page_as_markup_and_only_strata_links_are_links(self):
        data = self.data()
        data["reports"][0]["headline"] = "<img src=x onerror=alert(1)>"
        data["reports"][0]["summary"] = "<script>alert(1)</script>"
        data["reports"][0]["source"]["url"] = "javascript:alert(1)"
        data["reports"][1]["source"]["url"] = "https://evil.example/x"
        html = self.render(data)
        self.assertNotIn("<img", html)
        self.assertNotIn("<script", html)
        self.assertNotIn("javascript:", html)
        self.assertNotIn("evil.example", html)

    def test_an_empty_file_says_so(self):
        self.assertIn("No report could be read", self.render({"reports": []}))


class Committed(unittest.TestCase):
    def test_the_committed_catalogue_has_its_provenance_and_stays_out_of_the_results(self):
        data = json.loads((ROOT / "docs" / "community.json").read_text())
        self.assertGreater(data["count"], 50)
        self.assertEqual(data["source"]["license"], "MIT")
        self.assertTrue(all(e["source"]["url"].startswith("https://github.com/Niko1221/Strata/") for e in data["reports"]))
        self.assertFalse(any("community" in p.name for p in (ROOT / "results").glob("*.result.json")))


if __name__ == "__main__":
    unittest.main()
