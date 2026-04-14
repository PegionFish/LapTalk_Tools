from __future__ import annotations

import unittest
from unittest.mock import patch

import hwinfo_plotter.app_about as app_about


class AppAboutTests(unittest.TestCase):
    def test_resolve_app_version_prefers_generated_build_hash(self) -> None:
        with patch.object(app_about, "GENERATED_BUILD_GIT_HASH", "abc123"):
            self.assertEqual(app_about.resolve_app_version(), "abc123")

    def test_resolve_app_version_falls_back_to_runtime_git_hash(self) -> None:
        with (
            patch.object(app_about, "GENERATED_BUILD_GIT_HASH", None),
            patch.object(app_about, "resolve_runtime_git_hash", return_value="def456"),
        ):
            self.assertEqual(app_about.resolve_app_version(), "def456")

    def test_about_info_matches_about_document_fields(self) -> None:
        with patch.object(app_about, "GENERATED_BUILD_GIT_HASH", "abc123"):
            about_info = app_about.get_app_about_info()

        self.assertEqual(about_info.app_name, "CSV可视化对比工具")
        self.assertEqual(about_info.version_label, "Version")
        self.assertEqual(about_info.version, "abc123")
        self.assertEqual(about_info.distribution_prefix, "Distributed under")
        self.assertEqual(about_info.license_link.label, "GPLv3")
        self.assertEqual(about_info.license_link.url, "https://www.gnu.org/licenses/gpl-3.0.html")
        self.assertEqual(about_info.repository_link.label, "GitHub Repository")
        self.assertEqual(about_info.repository_link.url, "https://github.com/PegionFish/LapTalk_Tools")
        self.assertEqual(about_info.author_prefix, "Author:")
        self.assertEqual(about_info.author_link.label, "PegionFish")
        self.assertEqual(about_info.author_link.url, "https://github.com/PegionFish")
        self.assertEqual(about_info.affiliation_text, "on behalf of")
        self.assertEqual(about_info.organization_link.label, "LapTalk")
        self.assertEqual(about_info.organization_link.url, "https://space.bilibili.com/3691008172754973")
        self.assertEqual(about_info.email_link.label, "Email")
        self.assertEqual(about_info.email_link.url, "mailto:boblao0714@gmail.com")

    def test_parse_about_document_replaces_placeholder_with_runtime_version(self) -> None:
        about_info = app_about.parse_about_document(app_about.DEFAULT_ABOUT_DOCUMENT, "def456")

        self.assertEqual(about_info.version, "def456")
        self.assertEqual(about_info.repository_link.url, "https://github.com/PegionFish/LapTalk_Tools")


if __name__ == "__main__":
    unittest.main()
