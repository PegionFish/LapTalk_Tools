from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from hwinfo_plotter.core import (
    ChartStyle,
    HWiNFOData,
    SensorColumn,
    build_figure,
    choose_best_decoding,
    decode_csv_bytes,
    format_compact_elapsed_time,
    load_hwinfo_csv,
    parse_numeric_value,
    render_figure_png_bytes,
    resolve_tick_interval_seconds,
    save_figure,
)


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

    def test_loads_sample_csv_with_preloaded_series(self) -> None:
        data = load_hwinfo_csv(SAMPLE_CSV, preload_numeric=True)

        self.assertEqual(len(data._series_cache), len(data.columns))
        self.assertTrue(data._all_series_preloaded)

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

    def test_render_figure_png_bytes_returns_png_signature(self) -> None:
        column_index = next(
            column.index
            for column in self.data.columns
            if len(self.data.extract_series(column.index)[1]) > 10
        )

        figure = build_figure(
            self.data,
            [column_index],
            title="In-Memory Preview",
            width_px=1280,
            height_px=720,
            dpi=120,
        )

        try:
            png_bytes = render_figure_png_bytes(figure)
        finally:
            figure.clear()

        self.assertTrue(png_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(png_bytes), 0)

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
                line_width=2.4,
                show_grid=False,
                show_legend=False,
            ),
        )
        axis = figure.axes[0]

        self.assertEqual(axis.get_title(), "自定义标题")
        self.assertEqual(axis.get_xlabel(), "")
        self.assertEqual(axis.get_ylabel(), "")
        self.assertFalse(axis.xaxis.get_offset_text().get_visible())
        self.assertEqual(axis.lines[0].get_linewidth(), 2.4)
        self.assertEqual(len(axis.lines), len(column_indices))
        self.assertIsNone(axis.get_legend())

    def test_compact_time_formatter_omits_zero_hour_component(self) -> None:
        self.assertEqual(format_compact_elapsed_time(65), "01:05")
        self.assertEqual(format_compact_elapsed_time(3605), "01:00:05")

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

    def test_extract_series_uses_cache(self) -> None:
        base_time = datetime(2026, 4, 13, 12, 0, 0)
        data = HWiNFOData(
            source_path=Path("synthetic.csv"),
            encoding="utf-8",
            headers=["Date", "Time", "CPU"],
            columns=[SensorColumn(index=2, name="CPU", occurrence=1, display_name="[002] CPU")],
            timestamps=[base_time + timedelta(seconds=index) for index in range(3)],
            rows=[
                ["13/04/2026", "12:00:00", "1.0"],
                ["13/04/2026", "12:00:01", "2.0"],
                ["13/04/2026", "12:00:02", "3.0"],
            ],
        )

        with patch("hwinfo_plotter.core.parse_numeric_value", wraps=parse_numeric_value) as mock_parse:
            first_series = data.extract_series(2)
            second_series = data.extract_series(2)

        self.assertIs(first_series, second_series)
        self.assertEqual(mock_parse.call_count, 3)

    def test_build_figure_applies_per_series_colors(self) -> None:
        base_time = datetime(2026, 4, 13, 12, 0, 0)
        timestamps = [base_time + timedelta(seconds=index) for index in range(3)]
        data = HWiNFOData(
            source_path=Path("synthetic.csv"),
            encoding="utf-8",
            headers=["Date", "Time", "CPU", "GPU"],
            columns=[
                SensorColumn(index=2, name="CPU", occurrence=1, display_name="[002] CPU"),
                SensorColumn(index=3, name="GPU", occurrence=1, display_name="[003] GPU"),
            ],
            timestamps=timestamps,
            rows=[
                ["13/04/2026", "12:00:00", "1.0", "2.0"],
                ["13/04/2026", "12:00:01", "2.0", "3.0"],
                ["13/04/2026", "12:00:02", "3.0", "4.0"],
            ],
        )

        figure = build_figure(
            data,
            [2, 3],
            width_px=1280,
            height_px=720,
            dpi=120,
            color_by_column={
                2: "#ff0000",
                3: "#00ff00",
            },
        )
        axis = figure.axes[0]

        self.assertEqual(axis.lines[0].get_color(), "#ff0000")
        self.assertEqual(axis.lines[1].get_color(), "#00ff00")

    def test_time_axis_uses_denser_ticks(self) -> None:
        base_time = datetime(2026, 4, 13, 12, 0, 0)
        timestamps = [base_time + timedelta(minutes=index) for index in range(31)]
        data = HWiNFOData(
            source_path=Path("synthetic.csv"),
            encoding="utf-8",
            headers=["Date", "Time", "CPU"],
            columns=[SensorColumn(index=2, name="CPU", occurrence=1, display_name="[002] CPU")],
            timestamps=timestamps,
            rows=[
                ["13/04/2026", f"12:{index:02d}:00", f"{float(index):.1f}"]
                for index in range(31)
            ],
        )

        figure = build_figure(
            data,
            [2],
            width_px=1280,
            height_px=720,
            dpi=120,
        )
        axis = figure.axes[0]
        locator = axis.xaxis.get_major_locator()
        tick_values = locator.tick_values(0, 30 * 60)
        minute_diffs = [
            (tick_values[index + 1] - tick_values[index]) / 60
            for index in range(len(tick_values) - 1)
        ]

        self.assertTrue(minute_diffs)
        self.assertLessEqual(min(minute_diffs), 2)

    def test_time_axis_supports_fixed_interval(self) -> None:
        base_time = datetime(2026, 4, 13, 12, 0, 0)
        timestamps = [base_time + timedelta(minutes=index) for index in range(31)]
        data = HWiNFOData(
            source_path=Path("synthetic.csv"),
            encoding="utf-8",
            headers=["Date", "Time", "CPU"],
            columns=[SensorColumn(index=2, name="CPU", occurrence=1, display_name="[002] CPU")],
            timestamps=timestamps,
            rows=[
                ["13/04/2026", f"12:{index:02d}:00", f"{float(index):.1f}"]
                for index in range(31)
            ],
        )

        figure = build_figure(
            data,
            [2],
            width_px=1280,
            height_px=720,
            dpi=120,
            style=ChartStyle(
                fixed_time_interval_seconds=120,
            ),
        )
        axis = figure.axes[0]
        locator = axis.xaxis.get_major_locator()
        tick_values = locator.tick_values(0, 30 * 60)
        minute_diffs = [
            (tick_values[index + 1] - tick_values[index]) / 60
            for index in range(len(tick_values) - 1)
        ]

        self.assertTrue(minute_diffs)
        self.assertTrue(all(abs(diff - 2) < 1e-6 for diff in minute_diffs))

    def test_auto_time_tick_interval_avoids_overcrowded_short_ranges(self) -> None:
        interval_seconds = resolve_tick_interval_seconds(703.705, 7)

        self.assertEqual(interval_seconds, 60)

    def test_build_figure_can_hide_time_and_value_axis_text(self) -> None:
        base_time = datetime(2026, 4, 13, 12, 0, 0)
        timestamps = [base_time + timedelta(seconds=index) for index in range(10)]
        data = HWiNFOData(
            source_path=Path("synthetic.csv"),
            encoding="utf-8",
            headers=["Date", "Time", "CPU"],
            columns=[SensorColumn(index=2, name="CPU", occurrence=1, display_name="[002] CPU")],
            timestamps=timestamps,
            rows=[
                ["13/04/2026", f"12:00:{index:02d}", f"{float(index):.1f}"]
                for index in range(10)
            ],
        )

        figure = build_figure(
            data,
            [2],
            width_px=1280,
            height_px=720,
            dpi=120,
            style=ChartStyle(
                show_time_axis=False,
                show_value_axis=False,
            ),
        )
        axis = figure.axes[0]

        self.assertTrue(axis.spines["bottom"].get_visible())
        self.assertTrue(axis.spines["left"].get_visible())
        self.assertTrue(axis.spines["top"].get_visible())
        self.assertTrue(axis.spines["right"].get_visible())
        self.assertTrue(axis.xaxis.get_major_ticks()[0].tick1line.get_visible())
        self.assertFalse(axis.xaxis.get_major_ticks()[0].label1.get_visible())
        self.assertTrue(axis.yaxis.get_major_ticks()[0].tick1line.get_visible())
        self.assertFalse(axis.yaxis.get_major_ticks()[0].label1.get_visible())

    def test_build_figure_trims_visible_range(self) -> None:
        base_time = datetime(2026, 4, 13, 12, 0, 0)
        timestamps = [base_time + timedelta(seconds=index) for index in range(10)]
        data = HWiNFOData(
            source_path=Path("synthetic.csv"),
            encoding="utf-8",
            headers=["Date", "Time", "CPU"],
            columns=[SensorColumn(index=2, name="CPU", occurrence=1, display_name="[002] CPU")],
            timestamps=timestamps,
            rows=[
                ["13/04/2026", f"12:00:{index:02d}", f"{float(index):.1f}"]
                for index in range(10)
            ],
        )

        figure = build_figure(
            data,
            [2],
            width_px=1280,
            height_px=720,
            dpi=120,
            visible_range_seconds=(2, 6),
        )
        axis = figure.axes[0]
        x_data = list(axis.lines[0].get_xdata())

        self.assertEqual(x_data[0], 2)
        self.assertEqual(x_data[-1], 6)
        self.assertEqual(axis.get_xlim(), (2, 6))


if __name__ == "__main__":
    unittest.main()
