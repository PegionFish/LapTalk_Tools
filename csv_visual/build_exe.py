from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


DEFAULT_EXE_NAME = "HWiNFO-CSV-Plotter"
BUILD_INFO_MODULE_NAME = "csv_visual_build_info"
UNKNOWN_GIT_HASH = "unknown"
FILENAME_GIT_HASH_LENGTH = 7


def get_project_dir() -> Path:
    return Path(__file__).resolve().parent


def build_paths(project_dir: Path) -> tuple[Path, Path, Path]:
    dist_dir = project_dir / "dist"
    build_root = project_dir / "build" / "pyinstaller"
    work_dir = build_root / "work"
    spec_dir = build_root / "spec"
    return dist_dir, work_dir, spec_dir


def get_generated_build_info_dir(project_dir: Path) -> Path:
    return project_dir / "build" / "pyinstaller" / "generated"


def build_add_data_argument(source_path: Path, destination: str) -> str:
    return f"{source_path}{os.pathsep}{destination}"


def find_git_worktree_dir(start_dir: Path) -> Path | None:
    resolved_start_dir = start_dir.resolve()
    for candidate in (resolved_start_dir, *resolved_start_dir.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def run_git_rev_parse_head(project_dir: Path, git_args: list[str] | None = None) -> str | None:
    command = ["git"]
    if git_args:
        command.extend(git_args)
    command.extend(["rev-parse", "HEAD"])

    try:
        completed = subprocess.run(
            command,
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        return None

    resolved_hash = completed.stdout.strip()
    return resolved_hash or None


def resolve_build_git_hash(project_dir: Path) -> str:
    for environment_name in ("CSV_VISUAL_BUILD_GIT_HASH", "GITHUB_SHA"):
        environment_value = os.environ.get(environment_name, "").strip()
        if environment_value:
            return environment_value

    resolved_hash = run_git_rev_parse_head(project_dir)
    if resolved_hash is not None:
        return resolved_hash

    worktree_dir = find_git_worktree_dir(project_dir)
    if worktree_dir is not None:
        resolved_hash = run_git_rev_parse_head(
            project_dir,
            ["-c", f"safe.directory={worktree_dir.as_posix()}"],
        )
        if resolved_hash is not None:
            return resolved_hash

    resolved_hash = run_git_rev_parse_head(project_dir, ["-c", "safe.directory=*"])
    if resolved_hash is not None:
        return resolved_hash

    return UNKNOWN_GIT_HASH


def format_git_hash_for_filename(git_hash: str) -> str:
    sanitized_hash = re.sub(r"[^0-9A-Za-z]+", "", git_hash.strip())
    if not sanitized_hash:
        return UNKNOWN_GIT_HASH
    return sanitized_hash[:FILENAME_GIT_HASH_LENGTH]


def build_versioned_exe_name(base_name: str, git_hash: str) -> str:
    return f"{base_name}-{format_git_hash_for_filename(git_hash)}"


def write_build_info_module(project_dir: Path, git_hash: str) -> Path:
    generated_dir = get_generated_build_info_dir(project_dir)
    generated_dir.mkdir(parents=True, exist_ok=True)
    build_info_path = generated_dir / f"{BUILD_INFO_MODULE_NAME}.py"
    build_info_path.write_text(f"BUILD_GIT_HASH = {git_hash!r}\n", encoding="utf-8")
    return build_info_path


def build_pyinstaller_command(
    project_dir: Path,
    exe_name: str = DEFAULT_EXE_NAME,
    *,
    onefile: bool = True,
    build_info_dir: Path | None = None,
) -> list[str]:
    dist_dir, work_dir, spec_dir = build_paths(project_dir)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        exe_name,
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work_dir),
        "--specpath",
        str(spec_dir),
        "--paths",
        str(project_dir),
        "--collect-data",
        "matplotlib",
        "--hidden-import",
        "matplotlib.backends.backend_agg",
        "--hidden-import",
        "matplotlib.backends.backend_tkagg",
        "--add-data",
        build_add_data_argument(project_dir / "about.md", "."),
    ]
    if build_info_dir is not None:
        command.extend(
            [
                "--paths",
                str(build_info_dir),
                "--hidden-import",
                BUILD_INFO_MODULE_NAME,
            ]
        )
    if onefile:
        command.append("--onefile")
    command.append(str(project_dir / "main.pyw"))
    return command


def resolve_output_path(
    project_dir: Path,
    exe_name: str = DEFAULT_EXE_NAME,
    *,
    onefile: bool = True,
) -> Path:
    dist_dir, _, _ = build_paths(project_dir)
    if onefile:
        return dist_dir / f"{exe_name}.exe"
    return dist_dir / exe_name / f"{exe_name}.exe"


def is_pyinstaller_available() -> bool:
    try:
        subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    return True


def format_command(command: list[str]) -> str:
    return subprocess.list2cmdline(command)


def print_console_line(text: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    safe_text = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
    print(safe_text, file=stream)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Package csv_visual as a Windows EXE.")
    parser.add_argument(
        "--name",
        default=DEFAULT_EXE_NAME,
        help=f"Executable name. Defaults to {DEFAULT_EXE_NAME}.",
    )
    parser.add_argument(
        "--onedir",
        action="store_true",
        help="Build in one-dir mode instead of a single-file EXE.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the PyInstaller command without running it.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project_dir = get_project_dir()
    onefile = not args.onedir
    git_hash = resolve_build_git_hash(project_dir)
    exe_name = build_versioned_exe_name(args.name, git_hash)
    build_info_path = write_build_info_module(project_dir, git_hash)
    command = build_pyinstaller_command(project_dir, exe_name, onefile=onefile, build_info_dir=build_info_path.parent)
    output_path = resolve_output_path(project_dir, exe_name, onefile=onefile)

    print_console_line(f"Version hash: {git_hash}")
    print_console_line(f"Executable name: {exe_name}")
    print_console_line(f"Output path: {output_path}")
    print_console_line(f"Command: {format_command(command)}")

    if args.dry_run:
        return 0

    if not is_pyinstaller_available():
        print_console_line(
            "PyInstaller is not installed. Run `pip install -r requirements.txt` in csv_visual/ and try again.",
            error=True,
        )
        return 1

    subprocess.run(command, cwd=project_dir, check=True)
    print_console_line(f"Build completed: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
