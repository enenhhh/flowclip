from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class EntrypointTests(unittest.TestCase):
    def test_package_smoke_imports_platform_runtime_without_creating_ui(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "desktop" / "main.py"), "--package-smoke"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
