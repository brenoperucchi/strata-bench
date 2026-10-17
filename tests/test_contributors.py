"""The thank-you page: who is listed, in what order, and that a declared name never reaches the page as markup."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


def run(js):
    r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


ROWS = [
    {"by": "ana", "gpu": "RTX 5090", "engine": "0.1.43", "date": "2026-10-12T10:00:00-03:00"},
    {"by": "ana", "gpu": "RTX 5090", "engine": "0.1.44", "date": "2026-10-14T10:00:00-03:00"},
    {"by": "bob", "gpu": "RX 9070 XT", "engine": "0.1.44", "date": "2026-10-13T10:00:00-03:00"},
    {"by": None, "gpu": "V100", "engine": "0.1.41", "date": "2026-10-01T10:00:00-03:00"},
]


@unittest.skipUnless(NODE, "node is not installed")
class Contributors(unittest.TestCase):
    def call(self, expr, rows=ROWS):
        return run(f"const C = require('./docs/contributors.js'); console.log(JSON.stringify({expr}));".replace("ROWS", json.dumps(rows)))

    def test_people_are_grouped_by_declared_name_with_the_most_results_first_and_a_result_without_a_name_is_left_out(self):
        got = self.call("C.people(ROWS).map(p => [p.name, p.results, p.gpus, p.engines, p.first, p.last])")
        self.assertEqual(got, [["ana", 2, ["RTX 5090"], ["0.1.43", "0.1.44"], "2026-10-12", "2026-10-14"], ["bob", 1, ["RX 9070 XT"], ["0.1.44"], "2026-10-13", "2026-10-13"]])

    def test_the_page_thanks_them_counts_them_and_links_each_name_to_their_results(self):
        html = self.call("C.render(ROWS)")
        self.assertIn("2 people so far, 3 results", html)
        self.assertIn("Thank you", html)
        self.assertIn("index.html?by=ana", html)

    def test_a_declared_name_never_reaches_the_page_as_markup(self):
        rows = [{"by": "<img src=x onerror=alert(1)>", "gpu": "<script>x</script>", "engine": "\"'><b>", "date": "2026-10-12"}]
        html = self.call("C.render(ROWS)", rows)
        for bad in ("<img", "<script", "<b>"):
            self.assertNotIn(bad, html)
        self.assertIn("&lt;img", html)
        self.assertIn("index.html?by=%3Cimg", html)                                   # the name in the link is URL-encoded

    def test_without_names_the_page_says_how_to_get_listed(self):
        self.assertIn("--by your-name", self.call("C.render([])"))

    def test_the_page_and_the_top_line_point_at_each_other(self):
        page = (ROOT / "docs" / "contributors.html").read_text()
        self.assertIn('<script src="contributors.js"></script>', page)
        self.assertIn('href="index.html"', page)
        self.assertEqual(subprocess.run([NODE, "--check", str(ROOT / "docs" / "contributors.js")], capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
