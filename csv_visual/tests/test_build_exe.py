from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from build_exe import (
    DEFAULT_EXE_NAME,
    build_paths,
    build_pyinstaller_command,
    print_console_line,
    resolve_output_path,
)


ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements.txt"


class BuildExeTests(unittest.TestCase):
    def test_requirements_include_runtime_and_pyinstaller(self) -> None:
        lines = [
            line.strip()
            for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

        self.assertTrue(any(line.startswith("matplotlib") for line in lines))
        self.assertTrue(any(line.startswith("pyinstaller") for line in lines))

    def test_print_console_line_handles_non_utf8_console(self) -> None:
        class FakeStream:
            encoding = "cp1252"

            def __init__(self) -> None:
                self.writes: list[str] = []

            def write(self, text: str) -> int:
                self.writes.append(text)
                return len(text)

            def flush(self) -> None:
                return None

        fake_stream = FakeStream()

        with patch("sys.stdout", fake_stream):
            print_console_line("目标输出：C:\\temp\\demo.exe")

        self.assertTrue(fake_stream.writes)
        self.assertIn("C:\\temp\\demo.exe", "".join(fake_stream.writes))

    def test_build_paths_use_project_local_directories(self) -> None:
        dist_dir, work_dir, spec_dir = build_paths(ROOT)

        self.assertEqual(dist_dir, ROOT / "dist")
        self.assertEqual(work_dir, ROOT / "build" / "pyinstaller" / "work")
        self.assertEqual(spec_dir, ROOT / "build" / "pyinstaller" / "spec")

    def test_build_pyinstaller_command_defaults_to_onefile(self) -> None:
        command = build_pyinstaller_command(ROOT)

        self.assertEqual(command[:3], [sys.executable, "-m", "PyInstaller"])
        self.assertIn("--onefile", command)
        self.assertIn("--windowed", command)
        self.assertIn("--collect-data", command)
        self.assertIn("matplotlib", command)
        self.assertIn(str(ROOT / "main.pyw"), command)

    def test_build_pyinstaller_command_supports_onedir_mode(self) -> None:
        command = build_pyinstaller_command(ROOT, "CustomApp", onefile=False)

        self.assertNotIn("--onefile", command)
        self.assertIn("CustomApp", command)

    def test_resolve_output_path_matches_packaging_mode(self) -> None:
        self.assertEqual(
            resolve_output_path(ROOT, DEFAULT_EXE_NAME, onefile=True),
            ROOT / "dist" / f"{DEFAULT_EXE_NAME}.exe",
        )
        self.assertEqual(
            resolve_output_path(ROOT, DEFAULT_EXE_NAME, onefile=False),
            ROOT / "dist" / DEFAULT_EXE_NAME / f"{DEFAULT_EXE_NAME}.exe",
        )


if __name__ == "__main__":
    unittest.main()
