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
