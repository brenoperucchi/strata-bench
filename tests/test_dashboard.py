"""The dashboard writes table cells as HTML, so a value from a result file must never reach a cell unescaped."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


def run_node(js: str) -> str:
    r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout


@unittest.skipUnless(NODE, "node is not installed")
class Dashboard(unittest.TestCase):
    def helpers(self):
        src = (ROOT / "docs" / "app.js").read_text()
        lines = [re.search(r"^\s*(var (?:esc|fmt) = function.*;)\s*$", ln) for ln in src.splitlines()]
        return "\n".join(m.group(1) for m in lines if m)

    def test_fmt_escapes_anything_that_is_not_a_number(self):
        payloads = ["<img src=x onerror=alert(1)>", '"><script>alert(1)</script>', "a & b", "'quote'", True, ["<b>"], {"a": "<i>"}]
        out = json.loads(run_node(self.helpers() + f"\nconsole.log(JSON.stringify({json.dumps(payloads)}.map((v) => fmt(v, 0))));"))
        for v, cell in zip(payloads, out):
            self.assertNotIn("<", cell, v)
            self.assertNotIn('"', cell, v)
        self.assertEqual(json.loads(run_node(self.helpers() + "\nconsole.log(JSON.stringify([fmt(null), fmt(undefined), fmt(12.345, 1), fmt(NaN), fmt(Infinity)]));"))[:3], ["–", "–", "12.3"])
        nan, inf = json.loads(run_node(self.helpers() + "\nconsole.log(JSON.stringify([fmt(NaN), fmt(Infinity)]));"))
        self.assertNotIn("<", nan + inf)

    def test_every_table_cell_built_from_a_result_goes_through_fmt_or_esc(self):
        src = (ROOT / "docs" / "app.js").read_text()
        for m in re.finditer(r'"<td[^"]*>" \+ ([^+]*)', src):
            expr = m.group(1).strip()
            self.assertTrue(expr.startswith(("fmt(", "esc(", "(", '"')) or expr in ("", ), expr)    # no bare field concatenated as HTML


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(NODE, "node is not installed")
class RenderedDashboard(unittest.TestCase):
    PAYLOAD = '<img src=x onerror=alert(1)>"\'>'

    def hostile(self, row):
        """Every field of a data.json row replaced by markup, as if a result that passed no gate had reached the page."""
        def poison(v):
            if isinstance(v, dict):
                return {k: poison(x) for k, x in v.items()}
            if isinstance(v, list):
                return [poison(x) for x in v]
            return self.PAYLOAD
        return poison(row)

    def render(self, rows):
        data = {"protocols": ["standard-v1", "standard-v2"], "count": len(rows), "results": rows}
        tmp = Path(__import__("tempfile").mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "data.json").write_text(json.dumps(data))
        r = subprocess.run([NODE, str(ROOT / "tests" / "js" / "render_dashboard.mjs"), str(ROOT / "docs" / "app.js"), str(tmp / "data.json")], capture_output=True, text=True, timeout=30)
        self.assertEqual((r.returncode, r.stderr), (0, ""), r.stderr)                      # a page that fails to draw must not pass as a safe one
        return json.loads(r.stdout)

    def test_a_row_made_of_markup_reaches_no_html_unescaped(self):
        sample = json.loads((ROOT / "docs" / "data.json").read_text())["results"][0]
        v2 = dict(sample, protocol="standard-v2", suite="full", host_relation="lan", id="results/x.result.json",
                  agent={"hit_pct": 99, "reread_tokens": 1, "ttft_turn1_ms": 1, "ttft_last_turn_ms": 1}, clients=[{"n": 8, "total_tps": 5, "ttft_p95_ms": 1, "errors": 0}])
        out = self.render([self.hostile(sample), self.hostile(v2), sample, v2])
        text = "\n".join(out.values())
        self.assertIn("<tr", text)                                                      # the table really was drawn
        self.assertNotIn("<img", text)
        self.assertNotIn("onerror=alert(1)>", text.replace("&gt;", ""))                  # the attribute text may appear only as escaped text
        self.assertIn("&lt;img", text)

    def test_the_plain_rows_still_render_their_numbers(self):
        sample = json.loads((ROOT / "docs" / "data.json").read_text())["results"][0]
        out = self.render([sample])
        self.assertIn("RTX 5090", out["perf"])
        self.assertIn("title='Click for more details'", out["perf"])                   # the hover hint on the row
        self.assertNotIn("<details>", out["perf"])                                      # the configuration lives on the details page, not in the table
        self.assertNotIn("details →", out["perf"])
