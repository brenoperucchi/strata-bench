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


if __name__ == "__main__":
    unittest.main()
