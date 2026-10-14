"""The "who won more measures?" count on the compare page: what counts as in front, what is a tie, what stays out."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


def doc(label, sizes=None, host="same-host", agent=None, clients=None):
    d = {"label": label, "runner": {"host_relation": host}, "sizes": {}}
    for name, f in (sizes or {}).items():
        d["sizes"][name] = {k: {"median": v} for k, v in f.items()}
    if agent is not None:
        d["agent"] = {"hit_pct": {"median": agent}}
    if clients is not None:
        d["clients"] = [{"n": 8, "total_tps": clients}]
    return d


MED = lambda p, dec, ms=None, ttft=None: {"medium": dict(prompt_tps=p, decode_tps=dec, **({"prompt_ms": ms} if ms else {}), **({"ttft_ms": ttft} if ttft else {}))}


@unittest.skipUnless(NODE, "node is not installed")
class Scoreboard(unittest.TestCase):
    def compute(self, docs):
        js = f"const S = require('./docs/scoreboard.js'); console.log(JSON.stringify(S.compute({json.dumps(docs)})));"
        r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_a_clearly_better_result_is_in_front_on_every_measure(self):
        out = self.compute([doc("a", MED(1000, 100)), doc("b", MED(2000, 200))])
        self.assertEqual(out["headline"]["kind"], "leader")
        self.assertEqual(out["headline"]["leaders"], [1])
        self.assertEqual([r["wins"] for r in out["results"]], [0, 2])
        self.assertEqual(out["results"][1]["support"], 100)
        self.assertEqual(out["results"][0]["support"], 50)

    def test_differences_inside_the_noise_band_are_ties_that_count_for_nobody(self):
        # prompt +4.9% (band 5), decode +9.9% (band 10)
        out = self.compute([doc("a", MED(1000, 100)), doc("b", MED(1049, 109.9))])
        self.assertEqual(out["headline"]["kind"], "all-ties")
        self.assertTrue(all(m["tie"] for m in out["metrics"]))
        self.assertEqual([r["wins"] for r in out["results"]], [0, 0])
        self.assertEqual([r["ties"] for r in out["results"]], [2, 2])

    def test_just_past_the_band_is_a_win(self):
        out = self.compute([doc("a", MED(1000, 100)), doc("b", MED(1051, 110.1))])
        self.assertEqual(out["headline"]["leaders"], [1])
        self.assertEqual(out["results"][1]["wins"], 2)

    def test_a_result_inside_the_band_of_the_best_shares_the_first_place(self):
        out = self.compute([doc("a", MED(2000, 200)), doc("b", MED(1980, 100)), doc("c", MED(1000, 50))])
        prompt = [m for m in out["metrics"] if m["key"] == "medium:prompt_tps"][0]
        self.assertEqual(prompt["front"], [True, True, False])
        self.assertFalse(prompt["tie"])
        self.assertEqual([r["wins"] for r in out["results"]], [2, 1, 0])

    def test_a_time_is_better_when_lower(self):
        out = self.compute([doc("a", {"probe": {"prompt_ms": 400}}), doc("b", {"probe": {"prompt_ms": 800}})])
        m = out["metrics"][0]
        self.assertEqual((m["better"], m["front"]), ("low", [True, False]))
        self.assertEqual(out["results"][1]["support"], 50)       # the best time divided by this one

    def test_a_measure_missing_from_one_result_stays_out_instead_of_counting_as_zero(self):
        a = doc("a", {**MED(1000, 100), "long": {"prompt_tps": 5000, "decode_tps": 90}})
        b = doc("b", MED(1000, 100))
        out = self.compute([a, b])
        self.assertEqual([m["key"] for m in out["metrics"]], ["medium:prompt_tps", "medium:decode_tps"])
        self.assertIn("Prompt speed · long", out["excluded"])

    def test_two_leaders_with_the_same_count_are_not_a_single_leader(self):
        a = doc("a", {"medium": {"prompt_tps": 2000, "decode_tps": 100}})
        b = doc("b", {"medium": {"prompt_tps": 1000, "decode_tps": 200}})
        out = self.compute([a, b])
        self.assertEqual((out["headline"]["kind"], out["headline"]["leaders"]), ("shared", [0, 1]))

    def test_first_token_times_are_left_out_when_the_clients_ran_in_different_places(self):
        a = doc("a", MED(1000, 100, ttft=500), host="same-host")
        b = doc("b", MED(1000, 100, ttft=900), host="lan")
        out = self.compute([a, b])
        self.assertNotIn("medium:ttft_ms", [m["key"] for m in out["metrics"]])
        self.assertTrue(any("First token" in e for e in out["excluded"]))
        same = self.compute([a, doc("c", MED(1000, 100, ttft=900), host="same-host")])
        self.assertIn("medium:ttft_ms", [m["key"] for m in same["metrics"]])

    def test_the_agent_cache_hit_rate_ties_within_two_points(self):
        out = self.compute([doc("a", agent=99.0), doc("b", agent=97.5)])
        self.assertEqual(out["headline"]["kind"], "all-ties")
        out = self.compute([doc("a", agent=99.0), doc("b", agent=96.0)])
        self.assertEqual(out["headline"]["leaders"], [0])

    def test_one_result_or_nothing_in_common_gives_no_count(self):
        self.assertIsNone(self.compute([doc("a", MED(1, 1))]))
        out = self.compute([doc("a", {"medium": {"prompt_tps": 1}}), doc("b", {"long": {"prompt_tps": 1}})])
        self.assertEqual((out["headline"]["kind"], out["metrics"]), ("none", []))

    def test_zero_values_do_not_divide_by_zero(self):
        out = self.compute([doc("a", {"probe": {"prompt_ms": 0}}), doc("b", {"probe": {"prompt_ms": 5}})])
        self.assertEqual(out["results"][1]["support"], 0)
        out = self.compute([doc("a", MED(0, 0)), doc("b", MED(0, 0))])
        self.assertEqual(out["headline"]["kind"], "all-ties")


class Page(unittest.TestCase):
    def test_the_compare_page_loads_the_scoreboard_before_its_own_script(self):
        html = (ROOT / "docs" / "compare.html").read_text()
        self.assertLess(html.index("scoreboard.js"), html.index("compare.js"))
        self.assertIn('id="scoreboard"', html)

    def test_the_compare_script_writes_result_text_only_with_textcontent(self):
        src = (ROOT / "docs" / "compare.js").read_text()
        self.assertNotIn("innerHTML", src)
        self.assertNotIn("insertAdjacentHTML", src)


if __name__ == "__main__":
    unittest.main()
