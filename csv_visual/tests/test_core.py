from __future__ import annotations

import unittest
from pathlib import Path

from hwinfo_plotter.core import ChartStyle, build_figure, choose_best_decoding, decode_csv_bytes, load_hwinfo_csv, save_figure


ROOT = Path(__file__).resolve().parents[1]
SAMPLE_CSV = ROOT / "R23-15.CSV"


class CoreSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = load_hwinfo_csv(SAMPLE_CSV)

    def test_loads_sample_csv(self) -> None:
        self.assertGreater(len(self.data.timestamps), 0)
        self.assertGreater(len(self.data.columns), 100)
        self.assertEqual(self.data.source_path.name, "R23-15.CSV")

    def test_exports_png(self) -> None:
        column_index = next(
            column.index
            for column in self.data.columns
            if len(self.data.extract_series(column.index)[1]) > 10
        )

        figure = build_figure(
            self.data,
            [column_index],
            title="Smoke Test",
            width_px=1280,
            height_px=720,
            dpi=120,
        )

        output_path = ROOT / "_smoke_chart.png"

        try:
            save_figure(figure, output_path)
            self.assertTrue(output_path.exists())
            self.assertGreater(output_path.stat().st_size, 0)
        finally:
            if output_path.exists():
                output_path.unlink()

    def test_build_figure_applies_style_options(self) -> None:
        column_indices = [
            column.index
            for column in self.data.columns
            if len(self.data.extract_series(column.index)[1]) > 10
        ][:2]

        figure = build_figure(
            self.data,
            column_indices,
            width_px=1280,
            height_px=720,
            dpi=120,
            style=ChartStyle(
                title="自定义标题",
                x_label="采样时间",
                y_label="读数",
                line_width=2.4,
                show_grid=False,
                show_legend=False,
            ),
        )
        axis = figure.axes[0]

        self.assertEqual(axis.get_title(), "自定义标题")
        self.assertEqual(axis.get_xlabel(), "采样时间")
        self.assertEqual(axis.get_ylabel(), "读数")
        self.assertEqual(axis.lines[0].get_linewidth(), 2.4)
        self.assertIsNone(axis.get_legend())

    def test_decodes_utf8_bom_chinese_headers(self) -> None:
        raw_bytes = 'Date,Time,"提交虚拟内存 [MB]"\n'.encode("utf-8-sig")

        text, encoding = decode_csv_bytes(raw_bytes)

        self.assertEqual(encoding, "utf-8-sig")
        self.assertIn("提交虚拟内存", text)

    def test_choose_best_decoding_prefers_readable_chinese(self) -> None:
        mojibake_text = 'Date,Time,"æ\x8f\x90äº¤è\x99\x9aæ\x8b\x9få\x86\x85å\xad\x98 [MB]"'
        readable_text = 'Date,Time,"提交虚拟内存 [MB]"'

        text, encoding = choose_best_decoding(
            [
                ("cp1252", mojibake_text),
                ("gb18030", readable_text),
            ]
        )

        self.assertEqual(encoding, "gb18030")
        self.assertEqual(text, readable_text)


if __name__ == "__main__":
    unittest.main()
