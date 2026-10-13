"""The detail page (docs/result.html + docs/result.js) renders a result file the validator accepted, but the file still holds free-text fields: nothing from it may be written as HTML."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "docs" / "result.js").read_text()
NODE = shutil.which("node")


class DetailPage(unittest.TestCase):
    def test_nothing_from_the_file_is_written_as_html(self):
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            self.assertNotIn(bad, JS)

    def test_the_page_loads_only_its_own_scripts(self):
        html = (ROOT / "docs" / "result.html").read_text()
        self.assertEqual(re.findall(r'<script[^>]*src="([^"]+)"', html), ["theme.js", "result.js"])
        self.assertEqual(re.findall(r"https?://[^\"' ]+\.js", JS), [])

    @unittest.skipUnless(NODE, "node is not installed")
    def test_the_script_parses(self):
        self.assertEqual(subprocess.run([NODE, "--check", str(ROOT / "docs" / "result.js")], capture_output=True).returncode, 0)

    @unittest.skipUnless(NODE, "node is not installed")
    def test_the_short_names_in_the_scripts_match_the_ones_the_site_builder_writes(self):
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        import build_site
        gpus = ("Tesla V100S-PCIE-32GB (reports as Tesla PG500-216)", "NVIDIA GeForce RTX 5090", "GeForce GTX 1060 6GB", "NVIDIA A100-SXM4-80GB", "Tesla V100-PCIE-16GB",
                "AMD Radeon RX 7900 XTX", "nvidia geforce rtx 4090", "(note only)", "NVIDIA")
        models = ("Swift (Qwen3.8-Flash-Next 125B MoE)", "Swift Qwen3.8 Flash Next", "(x)")
        for script in ("result.js", "compare.js"):
            src = (ROOT / "docs" / script).read_text()
            g, m = (re.search(r"var %s = (function .*?);\n" % n, src).group(1) for n in ("shortGpu", "shortModel"))
            code = "const g = %s, m = %s; console.log(JSON.stringify([%s, %s]));" % (g, m, json.dumps(list(gpus)) + ".map(g)", json.dumps(list(models)) + ".map(m)")
            out = json.loads(subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True).stdout)
            self.assertEqual(out[0], [build_site.gpu_name(n) for n in gpus], script)
            self.assertEqual(out[1], [build_site.model_name(n) for n in models], script)

    def test_the_id_check_accepts_published_results_and_refuses_anything_else(self):
        m = re.search(r"/(\^results.*?result\\\.json\$)/\.test\(id\)", JS)
        self.assertIsNotNone(m)
        rx = re.compile(m.group(1).replace("\\/", "/"))
        for r in json.loads((ROOT / "docs" / "data.json").read_text())["results"]:
            self.assertRegex(r["id"], rx)
        for bad in ("", "index.html", "results/../data.json", "http://evil.example/x.result.json", "/etc/passwd", "results/x.json", "results/a b.result.json"):
            self.assertFalse(rx.search(bad) and ".." not in bad, bad)

    def test_the_dashboard_links_to_the_detail_page_and_the_published_copy(self):
        app = (ROOT / "docs" / "app.js").read_text()
        self.assertIn("result.html?id=", app)
        self.assertNotIn("'../", app)                                  # Pages publishes only docs/: a link to ../results would be a 404


CMP = (ROOT / "docs" / "compare.js").read_text()


class ComparePage(unittest.TestCase):
    def test_nothing_from_the_files_is_written_as_html(self):
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            self.assertNotIn(bad, CMP)

    def test_the_page_loads_only_its_own_scripts(self):
        html = (ROOT / "docs" / "compare.html").read_text()
        self.assertEqual(re.findall(r'<script[^>]*src="([^"]+)"', html), ["theme.js", "compare.js"])
        self.assertEqual(re.findall(r"https?://[^\"' ]+\.js", CMP), [])

    @unittest.skipUnless(NODE, "node is not installed")
    def test_the_script_parses(self):
        self.assertEqual(subprocess.run([NODE, "--check", str(ROOT / "docs" / "compare.js")], capture_output=True).returncode, 0)

    def test_at_most_three_results_and_only_published_paths(self):
        self.assertIn("var MAX = 3;", CMP)
        m = re.search(r"ID_RX = /(\^results.*?result\\\.json\$)/;", CMP)
        self.assertIsNotNone(m)
        rx = re.compile(m.group(1).replace("\\/", "/"))
        for r in json.loads((ROOT / "docs" / "data.json").read_text())["results"]:
            self.assertRegex(r["id"], rx)
        for bad in ("results/../x.result.json", "http://evil.example/x.result.json", "/etc/passwd", "results/a b.result.json"):
            self.assertFalse(rx.search(bad) and ".." not in bad, bad)

    def test_the_dashboard_offers_the_comparison(self):
        app = (ROOT / "docs" / "app.js").read_text()
        self.assertIn("compare.html?ids=", app)
        self.assertIn("MAX_COMPARE = 3", app)
        self.assertIn('id="compare-go"', (ROOT / "docs" / "index.html").read_text())


if __name__ == "__main__":
    unittest.main()
