import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_version.py"
SPEC = importlib.util.spec_from_file_location("check_release_version", SCRIPT)
check_version = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_version)


class ReleaseTests(unittest.TestCase):
    def test_matching_stable_tag_and_untagged_build(self):
        self.assertEqual(check_version.validate("1.3.0", "v1.3.0"), "1.3.0")
        self.assertEqual(check_version.validate("0.0.0"), "0.0.0")

    def test_mismatched_or_noncanonical_tags_are_rejected(self):
        for tag in ("v1.3.1", "1.3.0", "v1.3.0-rc.1", "v1.3.0\n", "v1.3.0; touch /tmp/unexpected"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                check_version.validate("1.3.0", tag)

    def test_invalid_package_versions_are_rejected(self):
        for version in ("01.3.0", "1.03.0", "1.3", "1.3.0-rc.1", "1.3.0+build.1", "1.3.0\n", "../1.3.0", None):
            with self.subTest(version=version), self.assertRaises(ValueError):
                check_version.validate(version)

    def test_cli_rejects_wrong_tag_without_packaging(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--tag", "v999.999.999"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("must match app/version.py", result.stderr)


if __name__ == "__main__":
    unittest.main()
