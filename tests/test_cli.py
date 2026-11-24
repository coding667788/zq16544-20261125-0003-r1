import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from folioimpact.cli import main

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "case_replay.json"


class CliTests(unittest.TestCase):
    def test_validate_main(self):
        rc = main(["validate", str(FIXTURES)])
        self.assertEqual(rc, 0)

    def test_bound_mitigate_crosscheck_output_files_are_utf8_json(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "nested" / "result.json"
            self.assertEqual(main(["bound", str(FIXTURES), "-o", str(out)]), 0)
            payload = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["bounds"])

            out2 = Path(td) / "mit.json"
            self.assertEqual(main(["mitigate", str(FIXTURES), "-o", str(out2)]), 0)
            mit = json.loads(out2.read_text(encoding="utf-8"))
            self.assertTrue(mit["feasible"])

            out3 = Path(td) / "check.json"
            self.assertEqual(main(["crosscheck", str(FIXTURES), "-o", str(out3)]), 0)
            check = json.loads(out3.read_text(encoding="utf-8"))
            self.assertTrue(check["ok"])

    def test_module_invocation(self):
        proc = subprocess.run(
            [sys.executable, "-m", "folioimpact", "validate", str(FIXTURES)],
            cwd=ROOT, text=True, capture_output=True, encoding="utf-8", timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('"ok": true', proc.stdout)

    def test_invalid_file_returns_json_error(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "bad.json"
            bad.write_text('{"schema":"wrong"}', encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-m", "folioimpact", "validate", str(bad)],
                cwd=ROOT, text=True, capture_output=True, encoding="utf-8", timeout=60,
            )
            self.assertEqual(proc.returncode, 2)
            err = json.loads(proc.stderr)
            self.assertFalse(err["ok"])


if __name__ == "__main__":
    unittest.main()
