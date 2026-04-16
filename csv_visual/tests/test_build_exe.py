from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from build_exe import (
    BUILD_INFO_MODULE_NAME,
    DEFAULT_EXE_NAME,
    build_add_data_argument,
    build_paths,
    build_pyinstaller_command,
    build_versioned_exe_name,
    format_git_hash_for_filename,
    main,
    print_console_line,
    resolve_build_git_hash,
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

    def test_resolve_build_git_hash_retries_with_safe_directory(self) -> None:
        repo_root = ROOT / "_test_output" / "test_build_exe_git_hash_repo"
        project_dir = repo_root / "csv_visual"
        if repo_root.exists():
            shutil.rmtree(repo_root)
        project_dir.mkdir(parents=True, exist_ok=True)
        (repo_root / ".git").write_text("gitdir: .fake\n", encoding="utf-8")

        expected_command = ["git", "-c", f"safe.directory={repo_root.resolve().as_posix()}", "rev-parse", "HEAD"]
        observed_commands: list[list[str]] = []

        def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            observed_commands.append(command)
            if command == ["git", "rev-parse", "HEAD"]:
                raise subprocess.CalledProcessError(returncode=128, cmd=command)
            if command == expected_command:
                return subprocess.CompletedProcess(command, 0, stdout="25433f8abcdef1234567890\n", stderr="")
            raise AssertionError(f"Unexpected git command: {command}")

        try:
            with patch.dict("os.environ", {}, clear=True):
                with patch("build_exe.subprocess.run", side_effect=fake_run):
                    self.assertEqual(resolve_build_git_hash(project_dir), "25433f8abcdef1234567890")
        finally:
            if repo_root.exists():
                shutil.rmtree(repo_root)

        self.assertEqual(observed_commands, [["git", "rev-parse", "HEAD"], expected_command])

    def test_build_versioned_exe_name_appends_short_hash(self) -> None:
        self.assertEqual(format_git_hash_for_filename("25433f8abcdef1234567890"), "25433f8")
        self.assertEqual(format_git_hash_for_filename("abc123"), "abc123")
        self.assertEqual(format_git_hash_for_filename("unknown"), "unknown")
        self.assertEqual(
            build_versioned_exe_name(DEFAULT_EXE_NAME, "25433f8abcdef1234567890"),
            f"{DEFAULT_EXE_NAME}-25433f8",
        )

    def test_main_dry_run_uses_hash_suffixed_name(self) -> None:
        project_dir = ROOT / "_test_output" / "test_build_exe_main_dry_run"
        if project_dir.exists():
            shutil.rmtree(project_dir)
        project_dir.mkdir(parents=True, exist_ok=True)
        try:
            with patch("build_exe.get_project_dir", return_value=project_dir):
                with patch("build_exe.resolve_build_git_hash", return_value="25433f8abcdef1234567890"):
                    with patch("build_exe.print_console_line") as mocked_print:
                        exit_code = main(["--dry-run"])

            self.assertEqual(exit_code, 0)
            expected_name = f"{DEFAULT_EXE_NAME}-25433f8"
            expected_output_path = resolve_output_path(project_dir, expected_name, onefile=True)
            build_info_path = (
                project_dir / "build" / "pyinstaller" / "generated" / f"{BUILD_INFO_MODULE_NAME}.py"
            )

            self.assertEqual(
                build_info_path.read_text(encoding="utf-8"),
                "BUILD_GIT_HASH = '25433f8abcdef1234567890'\n",
            )
            self.assertIn(
                f"Executable name: {expected_name}",
                [call.args[0] for call in mocked_print.call_args_list],
            )
            self.assertIn(
                f"Output path: {expected_output_path}",
                [call.args[0] for call in mocked_print.call_args_list],
            )
        finally:
            if project_dir.exists():
                shutil.rmtree(project_dir)

    def test_write_build_info_module_uses_project_build_directory(self) -> None:
        project_dir = ROOT / "_test_output" / "test_build_exe_write_build_info"
        if project_dir.exists():
            shutil.rmtree(project_dir)
        project_dir.mkdir(parents=True, exist_ok=True)
        try:
            build_info_path = write_build_info_module(project_dir, "abc123")

            self.assertEqual(
                build_info_path,
                project_dir / "build" / "pyinstaller" / "generated" / f"{BUILD_INFO_MODULE_NAME}.py",
            )
            self.assertEqual(build_info_path.read_text(encoding="utf-8"), "BUILD_GIT_HASH = 'abc123'\n")
        finally:
            if project_dir.exists():
                shutil.rmtree(project_dir)

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
