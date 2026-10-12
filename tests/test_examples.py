"""The documented example identity really produces a publishable result: nothing in it is dropped and the validator accepts the file."""
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from strata_bench import core, validate
from tests import test_core as TC

ROOT = Path(__file__).resolve().parent.parent


class Examples(unittest.TestCase):
    def test_the_example_identity_is_accepted_whole(self):
        TC.Fake.reset()
        srv = TC.HTTPServer(("127.0.0.1", 0), TC.Fake)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        ident = json.loads((ROOT / "examples" / "identity.example.json").read_text())
        core.run(f"http://127.0.0.1:{srv.server_port}", TC.CFG, "setup-default", ["medium"], "ex", tmp, ident, log=lambda *a: None)
        d = json.loads((tmp / "ex.result.json").read_text())
        self.assertEqual(validate.problems(d), [])
        self.assertEqual([w for w in d["validity"]["warnings"] if "dropped" in w], [])         # every key of the example is in the public schema
        self.assertEqual(d["machine"]["gpus"][0]["name"], "RTX 5090")

    def test_the_documented_commands_exist(self):
        text = (ROOT / "docs" / "SUBMIT.md").read_text() + (ROOT / "README.md").read_text()
        for flag in ("--base", "--config", "--origin", "--label", "--out", "--identity", "--sizes", "--api-key", "--protocol", "--suite", "--host-relation"):
            self.assertIn(flag, text)
        import subprocess
        import sys
        h = subprocess.run([sys.executable, "-m", "strata_bench", "run", "--help"], capture_output=True, text=True, cwd=ROOT).stdout
        for flag in ("--base", "--config", "--origin", "--label", "--out", "--identity", "--sizes", "--api-key"):
            self.assertIn(flag, h)


if __name__ == "__main__":
    unittest.main()
