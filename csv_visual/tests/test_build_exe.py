from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from build_exe import (
    BUILD_INFO_MODULE_NAME,
    DEFAULT_EXE_NAME,
    build_add_data_argument,
    build_paths,
    build_pyinstaller_command,
    resolve_build_git_hash,
    print_console_line,
    resolve_output_path,
    write_build_info_module,
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
        self.assertIn(build_add_data_argument(ROOT / "about.md", "."), command)
        self.assertIn(str(ROOT / "main.pyw"), command)

    def test_build_pyinstaller_command_includes_generated_build_info_when_provided(self) -> None:
        build_info_dir = ROOT / "build" / "pyinstaller" / "generated"

        command = build_pyinstaller_command(ROOT, build_info_dir=build_info_dir)

        self.assertIn(str(build_info_dir), command)
        self.assertIn("--hidden-import", command)
        self.assertIn(BUILD_INFO_MODULE_NAME, command)

    def test_build_pyinstaller_command_supports_onedir_mode(self) -> None:
        command = build_pyinstaller_command(ROOT, "CustomApp", onefile=False)

        self.assertNotIn("--onefile", command)
        self.assertIn("CustomApp", command)

    def test_resolve_build_git_hash_prefers_environment_value(self) -> None:
        with patch.dict("os.environ", {"CSV_VISUAL_BUILD_GIT_HASH": "abc123"}, clear=False):
            self.assertEqual(resolve_build_git_hash(ROOT), "abc123")

    def test_write_build_info_module_uses_project_build_directory(self) -> None:
        with TemporaryDirectory() as temporary_dir:
            project_dir = Path(temporary_dir)

            build_info_path = write_build_info_module(project_dir, "abc123")

            self.assertEqual(
                build_info_path,
                project_dir / "build" / "pyinstaller" / "generated" / f"{BUILD_INFO_MODULE_NAME}.py",
            )
            self.assertEqual(build_info_path.read_text(encoding="utf-8"), "BUILD_GIT_HASH = 'abc123'\n")

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
