"""The overview at the top of the dashboard: what it concludes from the results, and that nothing from a result file reaches its HTML unescaped."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
PAYLOAD = '<img src=x onerror=alert(1)>"\'>'


def run_node(js: str):
    r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def row(id, **kw):
    """A data.json row with only the fields the overview reads."""
    base = {"id": id, "status": "ok", "protocol": "standard-v1", "gpu": "RTX 5090", "cpu": "Ryzen", "ram_gb": 96, "machine_id": "m1", "model": "Swift", "model_full": "Swift", "quant": "IQ3_XXS",
            "engine": "0.1.42", "config_id": "c1", "n_ctx": 32768, "date": "2026-10-11T10:00:00-03:00", "by": "someone", "args": ["--kv", "int8"],
            "sizes": {"medium": {"prompt_tps": 1000.0, "decode_tps": 100.0, "prompt_ms": 2000.0}, "long": {"prompt_tps": 2000.0, "decode_tps": 90.0, "prompt_ms": 8000.0}}}
    base.update(kw)
    return base


def scaled(rows_args, **kw):
    """Same as row() but with the speeds multiplied: prompt by kw['p'], decode by kw['d']."""
    p, d = kw.pop("p", 1.0), kw.pop("d", 1.0)
    r = row(**rows_args, **kw)
    for s in r["sizes"].values():
        s["prompt_tps"] *= p
        s["decode_tps"] *= d
    return r


@unittest.skipUnless(NODE, "node is not installed")
class Insights(unittest.TestCase):
    def call(self, expr: str, rows):
        return run_node(f"const I = require('./docs/insights.js'); const rows = {json.dumps(rows)}; console.log(JSON.stringify({expr}));")

    def test_a_new_build_on_the_same_machine_and_config_is_a_pair(self):
        a = row("a", date="2026-10-11T10:00:00-03:00")
        b = scaled(dict(id="b", engine="0.1.43.1", date="2026-10-12T10:00:00-03:00"), p=1.0, d=1.0)
        pairs = self.call("I.pairs(rows).map(p => [p.kind, p.a.engine, p.b.engine, p.moved, p.headline])", [a, b])
        self.assertEqual(pairs, [["build", "0.1.42", "0.1.43.1", 0, "inside the noise band"]])

    def test_a_change_exactly_at_the_band_is_noise_and_just_past_it_is_not(self):
        a = row("a")
        def verdicts(p, d):
            b = scaled(dict(id="b", engine="0.1.43", date="2026-10-12T10:00:00-03:00"), p=p, d=d)
            return self.call("I.pairs(rows)[0].deltas.map(d => d.verdict)", [a, b])
        self.assertEqual(verdicts(1.05, 1.10), ["same"] * 4)                     # prompt +5.0%, decode +10.0%
        self.assertEqual(verdicts(1.051, 1.101), ["up", "up", "up", "up"])        # +5.1% and +10.1%
        self.assertEqual(verdicts(0.949, 0.899), ["down"] * 4)

    def test_engine_versions_sort_by_number_not_by_text(self):
        self.assertEqual(self.call("['0.1.43.1', '0.1.9', '0.1.42', '0.1.10'].sort(I.engineCmp)", []), ["0.1.9", "0.1.10", "0.1.42", "0.1.43.1"])

    def test_two_configs_on_the_same_engine_are_a_config_pair_with_the_argument_changes(self):
        a = row("a", config_id="c1", args=["--kv", "int8", "--prefill", "auto", "--vision"])
        b = row("b", config_id="c2", date="2026-10-12T10:00:00-03:00", args=["--kv", "int8", "--prefill", "auto:32768", "--pcie-frac", "0.35"])
        got = self.call("I.pairs(rows).map(p => [p.kind, p.args])", [a, b])
        self.assertEqual(got[0][0], "config")
        self.assertEqual(got[0][1], [{"opt": "--pcie-frac", "from": None, "to": "0.35"}, {"opt": "--prefill", "from": "auto", "to": "auto:32768"}, {"opt": "--vision", "from": True, "to": None}])

    def test_results_of_different_machines_are_never_paired(self):
        a = row("a")
        b = row("b", machine_id="m2", gpu="RTX 3090", engine="0.1.43", date="2026-10-12T10:00:00-03:00")
        self.assertEqual(self.call("I.pairs(rows)", [a, b]), [])

    def test_a_row_without_a_machine_id_joins_the_machine_with_the_same_gpu_cpu_and_ram(self):
        a, b = row("a"), row("b", machine_id=None, engine="0.1.43", date="2026-10-12T10:00:00-03:00")
        keys = self.call("I.machineKeys(rows)", [a, b])
        self.assertEqual(keys["a"], keys["b"])
        self.assertEqual(len(self.call("I.pairs(rows)", [a, b])), 1)

    def test_different_protocols_are_never_compared(self):
        a = row("a")
        b = row("b", protocol="standard-v2", engine="0.1.43", date="2026-10-12T10:00:00-03:00")
        self.assertEqual(self.call("I.pairs(rows)", [a, b]), [])

    def test_a_failed_result_is_left_out_of_every_comparison(self):
        a, b = row("a"), row("b", status="failed", engine="0.1.43", date="2026-10-12T10:00:00-03:00")
        self.assertEqual(self.call("I.pairs(rows)", [a, b]), [])
        self.assertEqual(self.call("I.speedPoints(rows).length", [a, b]), 1)

    def test_a_release_needs_three_machines_before_it_is_more_than_an_observation(self):
        rows = []
        for i in range(3):
            rows.append(row(f"a{i}", machine_id=f"m{i}", cpu=f"cpu{i}"))
            rows.append(row(f"b{i}", machine_id=f"m{i}", cpu=f"cpu{i}", engine="0.1.43", date="2026-10-12T10:00:00-03:00"))
        self.assertEqual(self.call("I.transitionSupport(I.pairs(rows))", rows), {"0.1.42 → 0.1.43": 3})
        self.assertEqual(self.call("I.MIN_MACHINES", []), 3)
        self.assertEqual(self.call("I.transitionSupport(I.pairs(rows))", rows[:2]), {"0.1.42 → 0.1.43": 1})

    def test_the_same_model_on_two_machines_is_matched_by_family_even_when_the_name_differs(self):
        a = row("a", model_full="Swift", machine_id="m1")
        b = row("b", model_full="Swift (Qwen3.8-Flash-Next 125B MoE)", machine_id="m2", gpu="Tesla V100S", config_id="c2", n_ctx=131072,
                sizes={"medium": {"prompt_tps": 300.0, "decode_tps": 30.0, "prompt_ms": 9000.0}, "long": {"prompt_tps": 600.0, "decode_tps": 28.0, "prompt_ms": 20000.0}})
        got = self.call("I.sameModel(rows).map(g => [g.machines.length, g.differs, g.long_tokens])", [a, b])
        self.assertEqual(got, [[2, ["context size", "configuration"], 12000]])        # the prompt size shown is the smaller one the two machines report
        # another engine build is another group: never mixed
        c = row("c", machine_id="m2", gpu="Tesla V100S", engine="0.1.43")
        self.assertEqual(self.call("I.sameModel(rows)", [a, c]), [])

    def test_the_summary_counts_machines_and_contributors(self):
        rows = [row("a"), row("b", by="other", machine_id="m2", cpu="x", date="2026-10-12T10:00:00-03:00", engine="0.1.43"), row("c", status="failed", by=None)]
        s = self.call("I.summary(rows)", rows)
        self.assertEqual((s["results"], s["failed"], s["machines"], s["contributors"], s["engines"]), (3, 1, 2, ["other", "someone"], ["0.1.42", "0.1.43"]))


@unittest.skipUnless(NODE, "node is not installed")
class OverviewHtml(unittest.TestCase):
    def render(self, rows):
        data = {"protocols": ["standard-v1", "standard-v2"], "count": len(rows), "results": rows}
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "data.json").write_text(json.dumps(data))
        r = subprocess.run([NODE, str(ROOT / "tests" / "js" / "render_dashboard.mjs"), str(ROOT / "docs" / "app.js"), str(tmp / "data.json")], capture_output=True, text=True, timeout=30)
        self.assertEqual((r.returncode, r.stderr), (0, ""), r.stderr)
        return json.loads(r.stdout)

    def rows(self):
        return [row("a"), scaled(dict(id="b", engine="0.1.43", date="2026-10-12T10:00:00-03:00", config_id="c1"), p=1.2),
                row("c", machine_id="m2", gpu="Tesla V100S", cpu="Xeon", config_id="c9", model_full="Swift (big)")]

    def test_the_overview_draws_its_panels_from_real_looking_rows(self):
        out = self.render(self.rows())
        self.assertIn("<svg", out["ov-map"])
        self.assertIn("Same machine, what changed", out["ov-pairs"])
        self.assertIn("Same model, different machines", out["ov-same"])
        self.assertIn("RTX 5090", out["ov-map"])
        self.assertIn("results", out["cards"])

    def test_text_from_a_result_never_reaches_the_overview_as_markup(self):
        hostile = []
        for i, r in enumerate(self.rows()):
            r = dict(r)
            for k in ("gpu", "cpu", "model", "model_full", "quant", "engine", "by", "machine_id", "id", "protocol", "config_id", "date"):
                r[k] = PAYLOAD + str(i) if k == "id" else PAYLOAD
            r["args"] = [PAYLOAD, PAYLOAD]
            hostile.append(r)
        out = self.render(hostile)
        text = "\n".join(out.values())
        self.assertNotIn("<img", text)
        self.assertNotIn("onerror=alert(1)>", text.replace("&gt;", ""))
        self.assertIn("&lt;img", text)

    def test_nothing_to_compare_says_so_instead_of_drawing_an_empty_chart(self):
        out = self.render([row("a")])
        self.assertIn("No pair yet", out["ov-pairs"])
        self.assertIn("Nothing to compare yet", out["ov-same"])

    def test_the_page_loads_the_insight_modules_before_the_app(self):
        html = (ROOT / "docs" / "index.html").read_text()
        self.assertLess(html.index("insights.js"), html.index("overview.js"))
        self.assertLess(html.index("overview.js"), html.index("app.js"))


if __name__ == "__main__":
    unittest.main()
