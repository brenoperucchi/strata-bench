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


def probe_row(id, ms, **kw):
    """A result with only the short prompts (gprobe), as a contributor who ran --sizes gprobe sends: prompt time, no speeds."""
    r = row(id, **kw)
    r["sizes"] = {k: {"prompt_ms": ms, "prompt_tps": None, "decode_tps": None} for k in ("gprobe150", "gprobe300", "gprobe450")}
    return r


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

    def test_two_binaries_with_one_version_number_are_two_builds_not_a_config_change(self):
        official = row("a", engine="0.1.43.1", binary="aa690ae6", config_id="c1", protocol="standard-v2")
        fork = row("b", engine="0.1.43.1", binary="dfc35851", config_id="c2", protocol="standard-v2", date="2026-10-12T10:00:00-03:00")
        got = self.call("I.pairs(rows).map(p => [p.kind, p.moved >= 0])", [official, fork])
        self.assertEqual(got, [["mixed", True]])                                       # different binary and different config: shown, but flagged as not isolating either cause
        same_cfg = row("c", engine="0.1.43.1", binary="dfc35851", config_id="c1", protocol="standard-v2", date="2026-10-12T10:00:00-03:00")
        got = self.call("I.pairs(rows).map(p => [p.kind, p.a.binary, p.b.binary])", [official, same_cfg])
        self.assertEqual(got, [["build", "aa690ae6", "dfc35851"]])                   # same config, other binary: a build pair, and no mixed pair beside it

    def test_the_same_model_on_two_machines_is_matched_by_family_even_when_the_name_differs(self):
        a = row("a", model_full="Swift", machine_id="m1")
        b = row("b", model_full="Swift (Qwen3.8-Flash-Next 125B MoE)", machine_id="m2", gpu="Tesla V100S", config_id="c2", n_ctx=131072,
                sizes={"medium": {"prompt_tps": 300.0, "decode_tps": 30.0, "prompt_ms": 9000.0}, "long": {"prompt_tps": 600.0, "decode_tps": 28.0, "prompt_ms": 20000.0}})
        got = self.call("I.sameModel(rows).map(g => [g.machines.length, g.differs, g.long_tokens])", [a, b])
        self.assertEqual(got, [[2, ["context size", "configuration"], 12000]])        # the prompt size shown is the smaller one the two machines report

    def test_machines_on_other_engine_builds_still_meet_and_the_page_says_so(self):
        a = row("a")
        c = row("c", machine_id="m2", gpu="Tesla V100S", engine="0.1.43")
        got = self.call("I.sameModel(rows).map(g => [g.machines.length, g.differs, g.engine.split(', ').sort()])", [a, c])
        self.assertEqual(got, [[2, ["engine build"], ["0.1.42", "0.1.43"]]])

    def test_the_build_most_machines_share_is_the_one_compared(self):
        old = row("a-old", date="2026-10-10T10:00:00-03:00")
        new = row("a-new", engine="0.1.43", date="2026-10-12T10:00:00-03:00")
        other = row("b", machine_id="m2", gpu="Tesla V100S", date="2026-10-11T10:00:00-03:00")           # the second machine only has 0.1.42
        got = self.call("I.sameModel(rows).map(g => [g.differs, g.machines.map(m => m.build).sort()])", [old, new, other])
        self.assertEqual(got, [[[], ["0.1.42", "0.1.42"]]])                                              # m1 is read on 0.1.42 too, not on its newer build

    def test_a_different_protocol_is_never_the_same_model_group(self):
        a = row("a")
        b = row("b", machine_id="m2", gpu="Tesla V100S", protocol="standard-v2")
        self.assertEqual(self.call("I.sameModel(rows)", [a, b]), [])

    def test_model_names_that_spell_one_model_differently_are_matched_and_other_models_are_not(self):
        names = ["Qwen3.8-Flash-Next", "Swift Qwen3.8 Flash Next", "Qwen3.8 Flash Next", "Swift (Qwen3.8-Flash-Next 125B MoE)", "Swift"]
        fam = self.call("rows.map(n => (I.kindOf({model_full: n, quant: 'Q', protocol: 'p'})))", names)
        self.assertEqual(len(set(fam)), 1, fam)
        other = self.call("['Coder', 'Llama 3 8B', 'Llama 3 70B'].map(n => I.kindOf({model_full: n, quant: 'Q', protocol: 'p'}))", [])
        self.assertEqual(len(set(other + fam[:1])), 4)                                                    # a size in the main name is a different model

    def test_a_build_and_a_config_that_changed_together_is_a_mixed_pair_and_never_a_release(self):
        a = row("a", engine="0.1.43", config_id="c1", n_ctx=131072, args=["--max-context", "131072"])
        b = row("b", engine="0.1.44", config_id="c2", n_ctx=262144, args=["--max-context", "262144"], date="2026-10-12T10:00:00-03:00")
        got = self.call("I.pairs(rows).map(p => [p.kind, p.args])", [a, b])
        self.assertEqual(got, [["mixed", [{"opt": "--max-context", "from": "131072", "to": "262144"}]]])
        self.assertEqual(self.call("I.releases(I.pairs(rows))", [a, b]), [])           # only pairs that isolate the build count toward a release

    def test_a_release_gathers_its_pairs_over_machines_and_is_a_verdict_only_with_enough_of_them(self):
        rows = []
        for i in range(3):
            rows.append(row(f"a{i}", machine_id=f"m{i}", cpu=f"cpu{i}"))
            rows.append(scaled(dict(id=f"b{i}", machine_id=f"m{i}", cpu=f"cpu{i}", engine="0.1.43", date="2026-10-12T10:00:00-03:00"), p=1.20))
        rel = self.call("I.releases(I.pairs(rows)).map(r => [r.key, r.machines, r.enough, r.moved, r.metrics.map(m => m.median), r.metrics.map(m => m.verdict)])", rows)
        self.assertEqual(rel, [["0.1.42 → 0.1.43", 3, True, 2, [20.0, 20.0, 0.0, 0.0], ["up", "up", "same", "same"]]])
        few = self.call("I.releases(I.pairs(rows)).map(r => [r.machines, r.enough])", rows[:2])
        self.assertEqual(few, [[1, False]])

    def test_results_with_only_the_short_prompts_are_compared_by_prompt_time_and_less_is_better(self):
        a = probe_row("a", 800.0, config_id="c1", args=["--vram-reserve-mib", "995"])
        b = probe_row("b", 740.0, config_id="c2", args=["--vram-reserve-mib", "2200"], date="2026-10-12T10:00:00-03:00")      # 7.5% less time
        got = self.call("I.pairs(rows).map(p => [p.kind, p.set, p.deltas.map(d => [d.key, d.pct, d.verdict]), p.args])", [a, b])
        self.assertEqual(got, [["config", "probe", [["probe150", -7.5, "up"], ["probe300", -7.5, "up"], ["probe450", -7.5, "up"]], [{"opt": "--vram-reserve-mib", "from": "995", "to": "2200"}]]])

    def test_a_longer_prompt_time_is_a_change_for_the_worse_and_the_band_is_5_percent(self):
        a = probe_row("a", 1000.0, config_id="c1")
        def verdicts(ms):
            b = probe_row("b", ms, config_id="c2", date="2026-10-12T10:00:00-03:00")
            return self.call("I.pairs(rows)[0].deltas.map(d => d.verdict)", [a, b])
        self.assertEqual(verdicts(1050.0), ["same"] * 3)                             # +5.0% is on the band
        self.assertEqual(verdicts(1051.0), ["down"] * 3)                             # +5.1% is past it, and it is worse
        self.assertEqual(verdicts(949.0), ["up"] * 3)

    def test_a_result_with_medium_and_long_is_compared_by_speed_even_when_it_has_short_prompts_too(self):
        a, b = row("a", config_id="c1"), row("b", config_id="c2", date="2026-10-12T10:00:00-03:00")
        for r in (a, b):
            r["sizes"]["gprobe150"] = {"prompt_ms": 800.0, "prompt_tps": None, "decode_tps": None}
        self.assertEqual(self.call("I.pairs(rows).map(p => [p.set, p.deltas.length])", [a, b]), [["speed", 4]])

    def test_a_release_of_short_prompt_results_is_its_own_row_with_the_same_machine_rule(self):
        rows = []
        for i in range(3):
            rows.append(probe_row(f"a{i}", 800.0, machine_id=f"m{i}", cpu=f"cpu{i}"))
            rows.append(probe_row(f"b{i}", 880.0, machine_id=f"m{i}", cpu=f"cpu{i}", engine="0.1.43", date="2026-10-12T10:00:00-03:00"))      # 10% longer
        got = self.call("I.releases(I.pairs(rows)).map(r => [r.set, r.key, r.machines, r.enough, r.metrics.map(m => [m.median, m.verdict])])", rows)
        self.assertEqual(got, [["probe", "0.1.42 → 0.1.43", 3, True, [[10.0, "down"]] * 3]])

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
        self.assertIn("What changed", out["ov-pairs"])
        self.assertIn("Releases", out["ov-pairs"])
        self.assertIn("By GPU", out["ov-pairs"])
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

    def test_what_changed_has_three_tabs_that_need_no_script_and_folds_long_lists(self):
        rows = [row("a0")]
        for i in range(1, 8):                                                        # seven machines, each with a new build: more rows than the cap
            rows.append(row(f"b{i}", machine_id=f"m{i}", cpu=f"cpu{i}", engine="0.1.40", date="2026-10-10T10:00:00-03:00"))
            rows.append(row(f"c{i}", machine_id=f"m{i}", cpu=f"cpu{i}", engine="0.1.43", date="2026-10-12T10:00:00-03:00"))
        html = self.render(rows)["ov-pairs"]
        for tab in ("ov-tab-rel", "ov-tab-gpu", "ov-tab-cfg"):
            self.assertIn(f"type='radio' name='ov-tab' id='{tab}'", html)
        self.assertEqual(html.count("<script"), 0)
        self.assertIn("<details class='gpu' open>", html)                            # only the first group is open
        self.assertEqual(html.count("<details class='gpu'"), 7)
        self.assertIn("Show 2 more machines", html)                                  # seven machines: five shown, two folded
        self.assertIn("inside the noise band", html)

    def test_the_speed_map_card_has_an_expand_button_and_the_app_wires_it(self):
        out = self.render(self.rows())
        self.assertIn("data-expand", out["ov-map"])
        self.assertIn("aria-expanded='false'", out["ov-map"])
        app = (ROOT / "docs" / "app.js").read_text()
        self.assertIn("data-expand", app)
        self.assertIn("Escape", app)

    def test_the_speed_map_axes_can_be_set_and_leave_out_what_falls_outside(self):
        rows = self.rows()
        js = ("const O = require('./docs/overview.js'); const rows = %s; const view = %s; const h = O.speedMap(rows, 900, view);"
              "console.log(JSON.stringify({dots: (h.match(/class='dot /g) || []).length, outside: (h.match(/(\\d+) of (\\d+) results are outside/) || []).slice(1), fit: O.fit(rows)}));")
        base = run_node(js % (json.dumps(rows), "{}"))
        self.assertEqual((base["dots"], base["outside"]), (3, []))
        zoom = run_node(js % (json.dumps(rows), json.dumps({"xmin": 1100, "xmax": 4000, "ymin": 50, "ymax": 300})))
        self.assertEqual((zoom["dots"], zoom["outside"]), (1, ["2", "3"]))           # only the result at 1,200 tok/s is inside; the other two are counted, not hidden
        bad = run_node(js % (json.dumps(rows), json.dumps({"xmin": 5, "xmax": 1})))
        self.assertEqual(bad["dots"], 3)                                             # an upper end below the lower end is ignored
        f = base["fit"]
        self.assertTrue(f["xmin"] > 0 and f["xmin"] <= 1000 and f["xmax"] >= 1200)   # the fit holds every point with room, and does not start at zero
        fitted = run_node(js % (json.dumps(rows), json.dumps(f)))
        self.assertEqual((fitted["dots"], fitted["outside"]), (3, []))

    def test_the_speed_map_tells_the_page_its_axes_and_has_zoom_buttons_on_both(self):
        h = run_node("const O = require('./docs/overview.js'); console.log(JSON.stringify(O.speedMap(%s, 800, {xmin: 500, xmax: 1500})));" % json.dumps(self.rows()))
        for k in ("x+", "x-", "y+", "y-"):
            self.assertIn(f"data-zoom='{k}'", h)
        self.assertIn("data-view='500,1500,0,", h)                                   # what the zoom buttons and the drag read: the range now on screen
        self.assertIn("data-plot='", h)
        short = run_node("const O = require('./docs/overview.js'); console.log(JSON.stringify([O.speedMap(%s, 800).match(/viewBox='0 0 800 (\\d+)'/)[1], O.speedMap(%s, 800, {}, 600).match(/viewBox='0 0 800 (\\d+)'/)[1]]));" % (json.dumps(self.rows()), json.dumps(self.rows())))
        self.assertLessEqual(int(short[0]), 270)                                     # the card stays low
        self.assertEqual(short[1], "600")                                            # opened over the page it takes the height it is given

    def opening_tab(self, location=None):
        data = {"protocols": ["standard-v1"], "count": 1, "results": [row("a")]}
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "data.json").write_text(json.dumps(data))
        args = [NODE, str(ROOT / "tests" / "js" / "render_dashboard.mjs"), str(ROOT / "docs" / "app.js"), str(tmp / "data.json")] + ([json.dumps(location)] if location else [])
        r = subprocess.run(args, capture_output=True, text=True, timeout=30)
        self.assertEqual((r.returncode, r.stderr), (0, ""), r.stderr)
        return json.loads(r.stdout)["__views"]

    def test_the_page_opens_on_the_dashboard_and_a_filter_link_opens_on_the_results(self):
        self.assertEqual(self.opening_tab(), "dash:shown perf:hidden health:hidden")
        self.assertEqual(self.opening_tab({"search": "?by=someone"}), "dash:hidden perf:shown health:hidden")           # a link about one person's results is about the table
        self.assertEqual(self.opening_tab({"search": "?machine=m1"}), "dash:hidden perf:shown health:hidden")
        self.assertEqual(self.opening_tab({"hash": "#health"}), "dash:hidden perf:hidden health:shown")
        self.assertEqual(self.opening_tab({"search": "?by=someone", "hash": "#dashboard"}), "dash:shown perf:hidden health:hidden")   # an explicit tab wins

    def test_the_tabs_are_dashboard_results_health_and_the_overview_lives_under_the_first(self):
        html = (ROOT / "docs" / "index.html").read_text()
        order = [html.index(f'id="tab-{t}"') for t in ("dash", "perf", "health")]
        self.assertEqual(order, sorted(order))
        self.assertIn(">Dashboard</button>", html)
        self.assertIn(">Results</button>", html)
        dash = html[html.index('id="view-dash"'):html.index('id="view-perf"')]
        self.assertIn('id="overview"', dash)
        self.assertIn('id="ov-map"', dash)
        self.assertLess(html.index('id="cards"'), html.index('id="tab-dash"'))                                            # the summary strip stays above the tabs

    def test_the_results_table_shows_the_quantisation_and_keeps_the_model_name_in_the_tooltip(self):
        out = self.render([row("a", model="Swift Qwen3.8 Flash Next", model_full="Swift Qwen3.8 Flash Next", quant="IQ3_XXS")])
        table = out["perf"]
        self.assertIn("<b>IQ3_XXS</b>", table)
        self.assertIn(">Quant", table)
        self.assertIn("title='Swift Qwen3.8 Flash Next'", table)                       # the model is one hover away
        self.assertNotIn(">Swift Qwen3.8 Flash Next<", table)                           # and no longer takes the width of a column

    def test_short_prompt_pairs_get_their_own_table_and_a_note_that_a_drop_is_good(self):
        rows = [probe_row("a", 800.0, config_id="c1"), probe_row("b", 740.0, config_id="c2", date="2026-10-12T10:00:00-03:00")]
        html = self.render(rows)["ov-pairs"]
        self.assertIn("Prompt time 150", html)
        self.assertIn("a drop in the time is an improvement", html)
        self.assertIn("-7.5%".replace("-", "\u2212"), html)                           # the change is shown as it is: less time is a negative number, in green
        self.assertIn("chip up", html)

    def test_the_page_loads_the_insight_modules_before_the_app(self):
        html = (ROOT / "docs" / "index.html").read_text()
        self.assertLess(html.index("insights.js"), html.index("overview.js"))
        self.assertLess(html.index("overview.js"), html.index("app.js"))


if __name__ == "__main__":
    unittest.main()
