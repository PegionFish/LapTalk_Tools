from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_ABOUT_DOCUMENT = """CSV可视化对比工具
Version: [Git Commit Hash]
Distributed under [GPLv3](https://www.gnu.org/licenses/gpl-3.0.html)
[GitHub Repository](https://github.com/PegionFish/LapTalk_Tools)
Author: [PegionFish](https://github.com/PegionFish) on behalf of [LapTalk](https://space.bilibili.com/3691008172754973)
[Email](mailto:boblao0714@gmail.com)
"""
UNKNOWN_BUILD_VERSION = "unknown"
MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

try:
    from csv_visual_build_info import BUILD_GIT_HASH as GENERATED_BUILD_GIT_HASH
except ImportError:
    GENERATED_BUILD_GIT_HASH = None


@dataclass(frozen=True)
class AboutLink:
    label: str
    url: str


@dataclass(frozen=True)
class AboutInfo:
    app_name: str
    version_label: str
    version: str
    distribution_prefix: str
    license_link: AboutLink
    repository_link: AboutLink
    author_prefix: str
    author_link: AboutLink
    affiliation_text: str
    organization_link: AboutLink
    email_link: AboutLink


def get_project_dir(search_path: Path | None = None) -> Path:
    if search_path is not None:
        return search_path.resolve()

    bundled_base_dir = getattr(sys, "_MEIPASS", None)
    if bundled_base_dir:
        return Path(bundled_base_dir).resolve()

    return Path(__file__).resolve().parents[1]


def get_git_worktree_dir(search_path: Path | None = None) -> Path:
    if search_path is not None:
        return search_path.resolve()
    return Path(__file__).resolve().parents[2]


def get_about_document_path(search_path: Path | None = None) -> Path:
    return get_project_dir(search_path) / "about.md"


def _normalize_git_hash(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def resolve_runtime_git_hash(search_path: Path | None = None) -> str | None:
    for environment_name in ("CSV_VISUAL_BUILD_GIT_HASH", "GITHUB_SHA"):
        environment_value = _normalize_git_hash(os.environ.get(environment_name))
        if environment_value is not None:
            return environment_value

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=get_git_worktree_dir(search_path),
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        return None

    return _normalize_git_hash(completed.stdout)


def resolve_app_version(search_path: Path | None = None) -> str:
    generated_hash = _normalize_git_hash(GENERATED_BUILD_GIT_HASH)
    if generated_hash is not None:
        return generated_hash

    runtime_hash = resolve_runtime_git_hash(search_path)
    if runtime_hash is not None:
        return runtime_hash

    return UNKNOWN_BUILD_VERSION


def read_about_document(search_path: Path | None = None) -> str:
    about_document_path = get_about_document_path(search_path)
    try:
        return about_document_path.read_text(encoding="utf-8")
    except OSError:
        return DEFAULT_ABOUT_DOCUMENT


def parse_markdown_link(text: str) -> AboutLink:
    match = MARKDOWN_LINK_PATTERN.search(text)
    if match is None:
        stripped_text = text.strip()
        return AboutLink(stripped_text, stripped_text)
    return AboutLink(match.group(1).strip(), match.group(2).strip())


def _text_before_first_link(text: str, fallback: str) -> str:
    match = MARKDOWN_LINK_PATTERN.search(text)
    if match is None:
        return fallback
    return text[: match.start()].strip() or fallback


def _text_between_links(text: str, first_match: re.Match[str], second_match: re.Match[str], fallback: str) -> str:
    return text[first_match.end() : second_match.start()].strip() or fallback


def parse_about_document(document_text: str, version: str) -> AboutInfo:
    lines = [line.strip() for line in document_text.splitlines() if line.strip()]
    fallback_lines = [line.strip() for line in DEFAULT_ABOUT_DOCUMENT.splitlines() if line.strip()]
    if len(lines) < len(fallback_lines):
        lines = fallback_lines

    version_label = lines[1].split(":", 1)[0].strip() if ":" in lines[1] else "Version"
    distribution_prefix = _text_before_first_link(lines[2], "Distributed under")
    author_prefix = _text_before_first_link(lines[4], "Author:")
    author_links = tuple(MARKDOWN_LINK_PATTERN.finditer(lines[4]))
    if len(author_links) >= 2:
        author_link = AboutLink(author_links[0].group(1).strip(), author_links[0].group(2).strip())
        organization_link = AboutLink(author_links[1].group(1).strip(), author_links[1].group(2).strip())
        affiliation_text = _text_between_links(lines[4], author_links[0], author_links[1], "on behalf of")
    else:
        author_link = parse_markdown_link("PegionFish")
        organization_link = parse_markdown_link("LapTalk")
        affiliation_text = "on behalf of"

    return AboutInfo(
        app_name=lines[0],
        version_label=version_label,
        version=version,
        distribution_prefix=distribution_prefix,
        license_link=parse_markdown_link(lines[2]),
        repository_link=parse_markdown_link(lines[3]),
        author_prefix=author_prefix,
        author_link=author_link,
        affiliation_text=affiliation_text,
        organization_link=organization_link,
        email_link=parse_markdown_link(lines[5]),
    )


def get_app_about_info(search_path: Path | None = None) -> AboutInfo:
    return parse_about_document(read_about_document(search_path), resolve_app_version(search_path))
