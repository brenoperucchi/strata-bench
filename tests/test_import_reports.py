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
        self.data = imp.build(self.tmp, min_complete=0)
        self.by = {e["id"]: e for e in self.data["reports"]}

    def test_only_community_folders_are_read_newest_first(self):
        self.assertEqual([e["id"] for e in self.data["reports"]], ["2026-10-08-community-titan", "2026-10-04-community-gre", "2026-10-02-community-plain"])
        self.assertEqual(self.data["count"], 3)

    def test_the_median_column_is_used_and_a_list_of_runs_is_never_taken_for_a_figure(self):
        e = self.by["2026-10-08-community-titan"]
        self.assertEqual((e["prompt_tps"]["4k"], e["decode_tps"], e["prompt_tps"]["32k"]), (922.6, 75.4, 1642.1))
        self.assertIsNone(e["prompt_tps"]["16k"])                          # nothing near 16K in this report: empty, never a neighbour
        self.assertEqual(e["rows"][0]["ttft_s"], None)                      # '3.46 / 3.33 / 3.32 s' lists runs and has no median column

    def test_a_label_keeps_its_underscores_and_the_first_number_of_a_cell_with_a_range_is_the_median(self):
        e = self.by["2026-10-04-community-gre"]
        self.assertEqual((e["rows"][0]["label"], e["prompt_tps"]["4k"], e["decode_tps"]), ("Coder IQ1_M", 1224.1, 48.5))

    def test_what_the_report_does_not_say_stays_empty(self):
        plain = self.by["2026-10-02-community-plain"]
        self.assertEqual((plain["rows"], plain["prompt_tps"]["4k"], plain["decode_tps"], plain["strata"], plain["humaneval"]), ([], None, None, None, None))

    def test_the_curated_index_gives_the_date_headline_and_summary(self):
        titan = self.by["2026-10-08-community-titan"]
        self.assertEqual(titan["date"], "2026-10-08")
        self.assertIn("TITAN RTX", titan["headline"])
        self.assertIn("262,144", titan["summary"])
        self.assertEqual(titan["context"], 262144)
        self.assertEqual(titan["quant"], "IQ3_S")
        self.assertEqual(titan["strata"], "0.1.40.3")
        self.assertEqual((titan["cpu"], titan["ram"], titan["gpu"], titan["vram_gb"]), ("Xeon E5-2696 v4", "125 GiB RAM", "2x TITAN RTX", 24))

    def test_humaneval_is_the_score_the_report_writes_and_a_description_of_the_test_is_not_one(self):
        self.assertEqual(imp.humaneval("- HumanEval (164, greedy, thinking off): **158/164**, 7.1 min."), "158/164")
        self.assertEqual(imp.humaneval("HumanEval pass@1: 87.5%"), "87.5%")
        self.assertIsNone(imp.humaneval("Coding uses preselected numeric HumanEval IDs 0-39. The common denominator is 40 evaluated tasks."))

    def test_the_prompt_size_columns_take_a_fresh_prompt_of_about_that_size_and_the_report_own_quant(self):
        rows = [{"label": "IQ2_XS", "prompt_tokens": 32768.0, "reused": 0.0, "prompt_tps": 2817.0, "decode_tps": 101.0},
                {"label": "IQ3_S", "prompt_tokens": 32768.0, "reused": 0.0, "prompt_tps": 2261.0, "decode_tps": 66.0},
                {"label": "IQ3_S warm", "prompt_tokens": 32900.0, "reused": 30000.0, "prompt_tps": 9000.0, "decode_tps": 66.0}]
        self.assertEqual(imp.pick_row(rows, "32k", "IQ3_S")["prompt_tps"], 2261.0)       # own quant, and not the warm one that reused the cache
        self.assertEqual(imp.pick_row(rows, "32k", None)["prompt_tps"], 2817.0)
        self.assertIsNone(imp.pick_row(rows, "4k", "IQ3_S"))

    def test_the_hardware_headline_is_split_into_cpu_ram_gpu_and_vram_and_the_version_is_found(self):
        hw = imp.hardware("RTX 5090 32 GB, Core Ultra 9 285K, 64 GB RAM", {})
        self.assertEqual((hw["gpu"], hw["vram_gb"], hw["cpu"], hw["ram"]), ("RTX 5090", 32, "Core Ultra 9 285K", "64 GB RAM"))
        self.assertEqual(imp.strata_version("Strata v0.1.41 source build"), "0.1.41")
        self.assertEqual(imp.strata_version("nothing here", "2026-10-08-community-x-0.1.40.3"), None)
        self.assertEqual(imp.strata_version("built from v0.1.38 plus two fixes"), "0.1.38")

    def test_a_report_that_states_fewer_than_nine_tenths_of_the_columns_is_left_out_and_the_rule_is_written_down(self):
        strict = imp.build(self.tmp)                                               # the default: 10 of the 11 columns
        self.assertEqual((strict["count"], strict["read"]), (1, 3))               # only the TITAN fixture states 10 of the 11; the other two state a handful
        self.assertEqual(strict["rule"], {"columns": 11, "min_fraction": 0.9, "min_columns": 10})
        half = imp.build(self.tmp, min_complete=0.2)
        self.assertEqual(half["count"], 2)                                         # the one with no table at all is below even that
        self.assertLess(imp.completeness(self.by["2026-10-02-community-plain"]), 0.2)

    def test_empty_parentheses_and_notes_after_the_amount_of_ram_are_cleaned(self):
        hw = imp.hardware("(rerun): 2x RTX 5060 Ti (), Ryzen 5 5600X, 128 GB RAM - Strata 0.1.41: one card", {})
        self.assertEqual((hw["gpu"], hw["ram"], hw["vram_gb"]), ("2x RTX 5060 Ti", "128 GB RAM", None))
        self.assertEqual(imp.hardware("Tesla V100-PCIE-32GB with 16 GB of system RAM", {})["gpu"], "Tesla V100-PCIE-")

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
            {"id": "a", "date": "2026-10-08", "headline": "2x TITAN RTX 24 GB", "strata": "0.1.40.3", "cpu": "Xeon E5-2696 v4", "ram": "125 GiB RAM", "gpu": "2x TITAN RTX", "vram_gb": 24, "quant": "IQ3_S", "context": 262144,
             "chat_decode_tps": 62.1, "prompt_tps": {"4k": 922.6, "16k": None, "32k": 1642.1, "128k": 1853.6}, "decode_tps": 75.4, "humaneval": "158/164", "summary": "s", "source": {"url": "https://github.com/Niko1221/Strata/tree/477ac13c/bench/results/a"}},
            {"id": "b", "date": "2026-10-04", "headline": "RX 7900 GRE", "strata": "0.1.9", "quant": None, "context": None, "prompt_tps": {}, "source": {"url": "https://github.com/Niko1221/Strata/tree/477ac13c/bench/results/b"}}]}

    def test_the_page_says_the_reports_are_not_comparable_and_lists_them(self):
        html = self.render(self.data())
        self.assertIn("Not comparable with the Results tab", html)
        self.assertIn("2x TITAN RTX 24 GB", html)
        self.assertIn("923", html)                                             # 922.6 tok/s, shown rounded
        self.assertIn("477ac13c", html)
        for col in ("Strata", "CPU", "RAM", "GPU", "Quant", "Context", ">4K<", ">32K<", ">128K<", "Decode"):
            self.assertIn(col, html)
        for col in ("Chat decode", ">16K<", "HumanEval"):                       # one or two reports state them: no column
            self.assertNotIn(col, html)
        self.assertIn("0.1.40.3", html)

    def test_the_filters_by_version_quantisation_and_speed_and_the_sorting_work_and_a_missing_value_goes_last(self):
        js = ("const C = require('./docs/community.js'); const d = %s; const ids = (o) => (C.render(d, o).match(/title='(?:[^']*)' rel/g) || []).length;"
              "const order = (o) => (C.render(d, o).match(/<td class='nowrap'>([^<]+)</g) || []).map(s => s.replace(/.*>/, '').replace('<', ''));"
              "console.log(JSON.stringify({opts: C.options(d.reports), v: C.filter(d.reports, {strata: '0.1.9'}).length, q: C.filter(d.reports, {quant: 'IQ3_S'}).length, s: C.filter(d.reports, {speeds: true}).length,"
              "p4: order({sort: {key: 'p4k', dir: -1}}), p4up: order({sort: {key: 'p4k', dir: 1}}), ver: order({sort: {key: 'strata', dir: -1}}), cmp: C.versionCmp('0.1.40.3', '0.1.9')}));" % json.dumps(self.data()))
        r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["opts"], {"strata": ["0.1.40.3", "0.1.9"], "quant": ["IQ3_S"]})   # newest version first
        self.assertEqual((out["v"], out["q"], out["s"]), (1, 1, 1))
        self.assertEqual(out["p4"], ["2026-10-08", "2026-10-04"])
        self.assertEqual(out["p4up"], ["2026-10-08", "2026-10-04"])                              # the report with no 4K speed stays last both ways
        self.assertEqual(out["ver"], ["2026-10-08", "2026-10-04"])
        self.assertGreater(out["cmp"], 0)                                                         # 0.1.40.3 is newer than 0.1.9: by number, not by text

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
        self.assertEqual(data["rule"]["min_columns"], 10)                    # only reports that state at least 10 of the 11 columns are listed
        self.assertGreater(data["read"], data["count"])
        self.assertGreater(data["count"], 10)
        self.assertEqual(data["source"]["license"], "MIT")
        self.assertTrue(all(e["source"]["url"].startswith("https://github.com/Niko1221/Strata/") for e in data["reports"]))
        self.assertFalse(any("community" in p.name for p in (ROOT / "results").glob("*.result.json")))


if __name__ == "__main__":
    unittest.main()
