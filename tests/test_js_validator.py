"""docs/validator.js (browser + upload worker) must return EXACTLY the problems strata_bench.validate returns, on real results and on every kind of tampering."""
import copy
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from strata_bench import core, validate
from tests import test_core as TC

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
IDENT = {"machine": {"gpus": [{"name": "RTX 5090", "vram_gb": 32}], "cpu": "Ryzen 9 5950X", "ram_gb": 96, "os": "Windows 11"},
         "engine": {"version": "0.1.40.2", "commit": "abc1234"}, "model": {"family": "Swift", "quant": "IQ3_XXS"}}


def real(tmp, label, mode=None, sizes=("medium", "probe", "gprobe"), **kw):
    TC.Fake.reset()
    TC.Fake.st["mode"], TC.Fake.st["mode_after"] = mode, 1
    TC.Fake.st.update(kw)
    srv = TC.HTTPServer(("127.0.0.1", 0), TC.Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        core.run(f"http://127.0.0.1:{srv.server_port}", TC.CFG, "setup-default", list(sizes), label, tmp, IDENT, log=lambda *a: None)
    finally:
        srv.shutdown()
        srv.server_close()
    return json.loads((tmp / f"{label}.result.json").read_text())


@unittest.skipUnless(NODE, "node is not installed")
class JsParity(unittest.TestCase):
    def cases(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        ok = real(tmp, "ok")
        failed = real(tmp, "failed", mode="crash")
        warn = real(tmp, "warn", sizes=("medium",), reused=500)
        TC.Fake.reset()
        inc = real(tmp, "inc", mode="bad_request")
        out = {"ok": ok, "failed": failed, "warnings": warn, "incomplete": inc, "empty": {}}

        def mut(name, fn, base=ok):
            d = copy.deepcopy(base)
            fn(d)
            out[name] = d
        mut("by set", lambda d: d["submission"].update(by="breno-p"))
        mut("by with space", lambda d: d["submission"].update(by="two words"))
        mut("by a number", lambda d: d["submission"].update(by=5))
        mut("by a list", lambda d: d["submission"].update(by=["x"]))
        mut("by too long", lambda d: d["submission"].update(by="a" * 40))
        mut("by 39 chars", lambda d: d["submission"].update(by="a" * 39))
        mut("by an email", lambda d: d["submission"].update(by="me@example.com"))
        mut("machine id set", lambda d: d["machine"].update(machine_id="rig-1"))
        mut("machine id bad", lambda d: d["machine"].update(machine_id="my rig"))
        mut("unknown top key", lambda d: d.update(hostname="my-box"))
        mut("unknown nested key", lambda d: d["machine"].update(serial="123"))
        mut("scalar where object", lambda d: d.update(machine="x"))
        mut("object where scalar", lambda d: d.update(label={"a": 1}))
        mut("list not list", lambda d: d.update(runs={"a": 1}))
        mut("bad hex", lambda d: d["runs"][0].update(request_sha256="not-hex"))
        mut("bad hex upper", lambda d: d["runs"][0].update(output_sha256="ABCDEF12"))
        mut("size key not allowed", lambda d: d["sizes"].update(giant={"prompt_tps": {"median": 1}}))
        mut("windows path", lambda d: d["config"].update(args=["--pack", "E:\\private\\pack"]))
        mut("absolute path", lambda d: d.update(label="/home/someone/run"))
        mut("private ip 192", lambda d: d.update(label="host 192.168.1.5"))
        mut("private ip 10", lambda d: d.update(label="host 10.0.0.1"))
        mut("private ip 172", lambda d: d.update(label="host 172.16.4.2"))
        mut("loopback", lambda d: d.update(label="127.0.0.1"))
        mut("token sk", lambda d: d["config"]["env"].update(X="sk-abcdefgh12345"))
        mut("token gh", lambda d: d.update(label="ghp_abcdefgh12345"))
        mut("network share", lambda d: d.update(label="\\\\server\\share\\x"))
        mut("two leaks", lambda d: d.update(label="E:\\a 10.1.2.3"))
        mut("schema id", lambda d: d.update(schema="strata-bench/v2"))
        mut("schema id missing", lambda d: d.pop("schema"))
        mut("protocol id", lambda d: d["protocol"].update(id="custom"))
        mut("protocol missing", lambda d: d.pop("protocol"))
        mut("prompt hash", lambda d: d["protocol"]["prompt_sha256"].update({"medium.txt": "0" * 64}))
        mut("prompt unknown", lambda d: d["protocol"]["prompt_sha256"].update({"extra.txt": "0" * 64}))
        mut("probe system", lambda d: d["protocol"].update(probe_system_sha256="0" * 64))
        mut("gprobe system", lambda d: d["protocol"]["gprobe"].update(system_sha256="0" * 64))
        mut("gprobe chars", lambda d: d["protocol"]["gprobe"]["chars"].update({"150": 1}))
        for st in ("running", "incomplete", None, 5, True, "OK"):
            mut(f"status {st!r}", lambda d, st=st: d["validity"].update(status=st))
        mut("status removed", lambda d: d["validity"].pop("status"))
        mut("failure bad kind", lambda d: d["validity"]["failure"].update(kind="meltdown"), failed)
        mut("failure kind missing", lambda d: d["validity"]["failure"].pop("kind"), failed)
        mut("failure removed", lambda d: d["validity"].pop("failure"), failed)
        mut("failure where path", lambda d: d["validity"]["failure"].update(where="E:\\x"), failed)
        mut("failure where free text", lambda d: d["validity"]["failure"].update(where="medium run 1; rm"), failed)
        mut("failure where missing", lambda d: d["validity"]["failure"].pop("where"), failed)
        mut("failure on ok", lambda d: d["validity"].update(failure={"kind": "crash", "where": "medium run 1"}))
        mut("failure not object", lambda d: d["validity"].update(failure="crash"), failed)
        mut("failure extra key", lambda d: d["validity"]["failure"].update(note="x"), failed)
        mut("errors with leak", lambda d: d["validity"].update(error="boom at E:\\x\\y.cu"), failed)
        mut("unicode label", lambda d: d.update(label="résultat ✓ 测试"))
        mut("quote in label with leak", lambda d: d.update(label="it's /usr/local/x"))
        return [{"name": k, "doc": v} for k, v in out.items()]

    def test_javascript_returns_exactly_the_python_problems_on_every_case(self):
        cases = self.cases()
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "cases.json").write_text(json.dumps(cases))
        r = subprocess.run([NODE, str(ROOT / "tests" / "js" / "run_cases.mjs"), str(tmp / "cases.json"), str(ROOT / "docs" / "schema.json")], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        js = json.loads(r.stdout)
        for c, got in zip(cases, js):
            self.assertEqual(got, validate.problems(c["doc"]), c["name"])
        self.assertGreater(sum(1 for c in cases if validate.problems(c["doc"])), 30)        # the tampering cases really are rejected
        self.assertEqual([c["name"] for c in cases if not validate.problems(c["doc"])], ["ok", "failed", "warnings", "by set", "by 39 chars", "machine id set", "unicode label"])     # and only genuine results (a non-ASCII label is fine) pass

    def test_the_schema_data_file_is_current(self):
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        import build_validator_data
        self.assertEqual(build_validator_data.main(["--check"]), 0)

    def test_the_python_validator_survives_a_non_object(self):
        for doc in ([], "x", 5, None):
            self.assertTrue(validate.problems(doc))


if __name__ == "__main__":
    unittest.main()
