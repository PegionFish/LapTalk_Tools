from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


DEFAULT_EXE_NAME = "HWiNFO-CSV-Plotter"


def get_project_dir() -> Path:
    return Path(__file__).resolve().parent


def build_paths(project_dir: Path) -> tuple[Path, Path, Path]:
    dist_dir = project_dir / "dist"
    build_root = project_dir / "build" / "pyinstaller"
    work_dir = build_root / "work"
    spec_dir = build_root / "spec"
    return dist_dir, work_dir, spec_dir


def build_pyinstaller_command(
    project_dir: Path,
    exe_name: str = DEFAULT_EXE_NAME,
    *,
    onefile: bool = True,
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
    ]
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
    command = build_pyinstaller_command(project_dir, args.name, onefile=onefile)
    output_path = resolve_output_path(project_dir, args.name, onefile=onefile)

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
