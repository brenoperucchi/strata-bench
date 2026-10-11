"""The upload worker and the upload page: the worker's node tests run here, and the page is checked for the properties that matter (no HTML injection from the file, validator shared with Python)."""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from strata_bench import validate

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node is not installed")
class Worker(unittest.TestCase):
    def test_the_worker_tests_pass(self):
        r = subprocess.run([NODE, "--test", *sorted(str(p) for p in (ROOT / "worker" / "test").glob("*.test.mjs"))], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])

    def test_the_workers_valid_fixture_really_is_valid_for_the_python_validator(self):
        doc = json.loads((ROOT / "worker" / "test" / "fixtures" / "valid.result.json").read_text())
        self.assertEqual(validate.problems(doc), [])

    def test_the_page_and_worker_scripts_are_syntactically_valid_modules(self):
        for f in ("docs/submit.js", "docs/validator.js", "docs/config.js", "worker/src/index.js"):
            r = subprocess.run([NODE, "--input-type=module", "--check"], input=(ROOT / f).read_text(), capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f + r.stderr)


class Page(unittest.TestCase):
    def test_nothing_from_the_uploaded_file_is_written_as_html(self):
        js = (ROOT / "docs" / "submit.js").read_text()
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            self.assertNotIn(bad, js)

    def test_the_page_loads_only_its_own_scripts_and_the_turnstile_script(self):
        html = (ROOT / "docs" / "submit.html").read_text()
        self.assertEqual(re.findall(r'<script[^>]*src="([^"]+)"', html), ["submit.js"])
        js = (ROOT / "docs" / "submit.js").read_text()
        self.assertEqual(re.findall(r"https://[a-z.]+/[^\"' ]*\.js[^\"' ]*", js), ["https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit"])

    def test_the_upload_is_off_until_the_worker_is_configured(self):
        cfg = (ROOT / "docs" / "config.js").read_text()
        self.assertRegex(cfg, r'workerUrl:\s*"(https://[^"]+)?"')

    def test_the_worker_only_accepts_the_pages_origin(self):
        toml = (ROOT / "worker" / "wrangler.toml").read_text()
        self.assertIn('ALLOWED_ORIGIN = "https://brenoperucchi.github.io"', toml)
        self.assertNotIn("TOKEN =", toml.replace("GITHUB_TOKEN     fine", ""))             # secrets live in wrangler secrets, never in the file

    def test_issue_template_asks_for_the_file_and_a_confirmation(self):
        t = (ROOT / ".github" / "ISSUE_TEMPLATE" / "submit-result.yml").read_text()
        self.assertIn("result.json", t)
        self.assertIn("required: true", t)


if __name__ == "__main__":
    unittest.main()
