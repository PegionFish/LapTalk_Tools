from __future__ import annotations

import csv
import math
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from typing import Sequence
from unittest.mock import patch

from matplotlib.colors import to_rgba

from hwinfo_plotter.core import (
    AlignedExtremaGroup,
    ChartStyle,
    DetectedExtremum,
    ExtremaAssignment,
    ExtremaDetectionConfig,
    ExtremaPointKey,
    HWiNFOData,
    LoadedCsvSession,
    SensorColumn,
    SeriesKey,
    build_assigned_curve_points,
    build_comparison_figure,
    build_comparison_output_name,
    build_figure,
    choose_best_decoding,
    compute_global_time_bounds,
    compute_session_active_timeline_range,
    compute_session_timeline_range,
    decode_csv_bytes,
    detect_extrema_for_sessions,
    detect_series_extrema,
    filter_visible_series,
    format_compact_elapsed_time,
    group_aligned_extrema,
    load_hwinfo_csv,
    normalize_offsets_for_reference,
    parse_numeric_value,
    render_figure_png_bytes,
    resolve_session_source_trim_range,
    resolve_tick_interval_seconds,
    save_figure,
)
from hwinfo_plotter.runtime_logging import configure_runtime_logging, shutdown_runtime_logging


ROOT = Path(__file__).resolve().parents[1]


def build_synthetic_csv_headers() -> list[str]:
    return [
        "Date",
        "Time",
        "CPU Package Power [W]",
        "GPU Core Load [%]",
        "Available Physical Memory [MB]",
        "Notes",
    ]


def build_synthetic_csv_rows(row_count: int = 24) -> list[list[str]]:
    base_time = datetime(2026, 4, 13, 12, 0, 0)
    rows: list[list[str]] = []
    for index in range(row_count):
        timestamp = base_time + timedelta(seconds=index * 30)
        rows.append(
            [
                timestamp.strftime("%d/%m/%Y"),
                timestamp.strftime("%H:%M:%S"),
                f"{55.0 + index * 0.5:.1f}",
                f"{30.0 + (index % 7) * 3.0:.1f}",
                f"{8192.0 - index * 12.5:.1f}",
                "stable" if index % 2 == 0 else "warming",
            ]
        )
    return rows


def write_synthetic_hwinfo_csv(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(build_synthetic_csv_headers())
        writer.writerows(build_synthetic_csv_rows())
    return destination


def build_loaded_session(
    session_id: str,
    alias: str,
    *,
    offset_seconds: float = 0.0,
    is_reference: bool = False,
    source_trim_start_seconds: float = 0.0,
    source_trim_end_seconds: float | None = None,
) -> LoadedCsvSession:
    base_time = datetime(2026, 4, 13, 12, 0, 0)
    data = HWiNFOData(
        source_path=Path(f"{alias}.csv"),
        encoding="utf-8",
        headers=["Date", "Time", "CPU", "GPU"],
        columns=[
            SensorColumn(index=2, name="CPU", occurrence=1, display_name="[002] CPU"),
            SensorColumn(index=3, name="GPU", occurrence=1, display_name="[003] GPU"),
        ],
        timestamps=[base_time + timedelta(seconds=index) for index in range(4)],
        rows=[
            ["13/04/2026", "12:00:00", "1.0", "11.0"],
            ["13/04/2026", "12:00:01", "2.0", "12.0"],
            ["13/04/2026", "12:00:02", "3.0", "13.0"],
            ["13/04/2026", "12:00:03", "4.0", "14.0"],
        ],
    )
    return LoadedCsvSession(
        session_id=session_id,
        alias=alias,
        data=data,
        offset_seconds=offset_seconds,
        is_reference=is_reference,
        source_trim_start_seconds=source_trim_start_seconds,
        source_trim_end_seconds=source_trim_end_seconds,
    )


def build_extrema_session(
    session_id: str,
    alias: str,
    cpu_values: Sequence[float],
    *,
    offset_seconds: float = 0.0,
    is_reference: bool = False,
    source_trim_start_seconds: float = 0.0,
    source_trim_end_seconds: float | None = None,
) -> LoadedCsvSession:
    base_time = datetime(2026, 4, 13, 12, 0, 0)
    rows = [
        [
            "13/04/2026",
            f"12:00:{index:02d}",
            f"{float(cpu_value):.2f}",
            "0.0",
        ]
        for index, cpu_value in enumerate(cpu_values)
    ]
    data = HWiNFOData(
        source_path=Path(f"{alias}.csv"),
        encoding="utf-8",
        headers=["Date", "Time", "CPU", "GPU"],
        columns=[
            SensorColumn(index=2, name="CPU", occurrence=1, display_name="[002] CPU"),
            SensorColumn(index=3, name="GPU", occurrence=1, display_name="[003] GPU"),
        ],
        timestamps=[base_time + timedelta(seconds=index) for index in range(len(cpu_values))],
        rows=rows,
    )
    return LoadedCsvSession(
        session_id=session_id,
        alias=alias,
        data=data,
        offset_seconds=offset_seconds,
        is_reference=is_reference,
        source_trim_start_seconds=source_trim_start_seconds,
        source_trim_end_seconds=source_trim_end_seconds,
    )


def build_detected_extremum(
    session_id: str,
    *,
    aligned_seconds: float,
    source_seconds: float | None = None,
    source_value: float = 1.0,
    prominence: float = 1.0,
    sample_index: int = 0,
    column_index: int = 2,
    kind: str = "peak",
) -> DetectedExtremum:
    resolved_source_seconds = aligned_seconds if source_seconds is None else source_seconds
    return DetectedExtremum(
        event_id=f"{session_id}:{column_index}:{kind}:{sample_index}",
        key=SeriesKey(session_id, column_index),
        kind=kind,
        source_seconds=float(resolved_source_seconds),
        aligned_seconds=float(aligned_seconds),
        source_value=float(source_value),
        prominence=float(prominence),
        sample_index=int(sample_index),
    )


class ExtremaCoreTests(unittest.TestCase):
    def test_detect_series_extrema_finds_obvious_peaks(self) -> None:
        detected_extrema = detect_series_extrema(
            SeriesKey("run_a", 2),
            [0, 1, 2, 3, 4, 5, 6],
            [0.0, 4.0, 0.0, 5.0, 0.0, 3.0, 0.0],
            mode="peak",
            min_distance_seconds=1.0,
            min_prominence=1.0,
        )

        self.assertEqual([extremum.kind for extremum in detected_extrema], ["peak", "peak", "peak"])
        self.assertEqual([extremum.sample_index for extremum in detected_extrema], [1, 3, 5])
        self.assertEqual([extremum.source_seconds for extremum in detected_extrema], [1.0, 3.0, 5.0])
        self.assertEqual([extremum.source_value for extremum in detected_extrema], [4.0, 5.0, 3.0])

    def test_detect_series_extrema_finds_obvious_valleys(self) -> None:
        detected_extrema = detect_series_extrema(
            SeriesKey("run_a", 2),
            [0, 1, 2, 3, 4, 5, 6],
            [5.0, 1.0, 5.0, 0.0, 5.0, 2.0, 5.0],
            mode="valley",
            min_distance_seconds=1.0,
            min_prominence=1.0,
        )

        self.assertEqual([extremum.kind for extremum in detected_extrema], ["valley", "valley", "valley"])
        self.assertEqual([extremum.sample_index for extremum in detected_extrema], [1, 3, 5])
        self.assertEqual([extremum.source_value for extremum in detected_extrema], [1.0, 0.0, 2.0])

    def test_detect_series_extrema_respects_min_distance_seconds(self) -> None:
        detected_extrema = detect_series_extrema(
            SeriesKey("run_a", 2),
            [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0],
            [0.0, 5.0, 0.0, 4.0, 0.0, 3.5, 0.0],
            mode="peak",
            min_distance_seconds=1.6,
            min_prominence=1.0,
        )

        self.assertEqual([extremum.sample_index for extremum in detected_extrema], [1, 5])

    def test_detect_series_extrema_respects_min_prominence(self) -> None:
        detected_extrema = detect_series_extrema(
            SeriesKey("run_a", 2),
            [0, 1, 2, 3, 4],
            [0.0, 1.0, 0.8, 5.0, 0.0],
            mode="peak",
            min_distance_seconds=1.0,
            min_prominence=1.0,
        )

        self.assertEqual([extremum.sample_index for extremum in detected_extrema], [3])
        self.assertGreater(detected_extrema[0].prominence, 1.0)

    def test_detect_extrema_for_sessions_uses_trim_and_offset(self) -> None:
        session = build_extrema_session(
            "run_a",
            "RunA",
            [0.0, 4.0, 0.0, 5.0, 0.0, 6.0, 0.0],
            offset_seconds=10.0,
            is_reference=True,
            source_trim_start_seconds=1.0,
            source_trim_end_seconds=4.0,
        )
        config = ExtremaDetectionConfig(
            enabled=True,
            source_series_keys=(SeriesKey("run_a", 2),),
            mode="peak",
            min_distance_seconds=1.0,
            min_prominence=1.0,
        )

        detected_extrema = detect_extrema_for_sessions((session,), config)

        self.assertEqual([extremum.sample_index for extremum in detected_extrema], [3])
        self.assertEqual(detected_extrema[0].source_seconds, 3.0)
        self.assertEqual(detected_extrema[0].aligned_seconds, 13.0)

    def test_group_aligned_extrema_groups_runs_with_reference_anchor(self) -> None:
        grouped_extrema = group_aligned_extrema(
            (
                build_detected_extremum("run_a", aligned_seconds=10.0, source_value=5.0, prominence=4.0, sample_index=10),
                build_detected_extremum("run_b", aligned_seconds=10.4, source_value=6.0, prominence=3.0, sample_index=20),
            ),
            alignment_tolerance_seconds=0.5,
            reference_session_id="run_a",
        )

        self.assertEqual(len(grouped_extrema), 1)
        self.assertEqual(grouped_extrema[0].kind, "peak")
        self.assertEqual(grouped_extrema[0].anchor_seconds, 10.0)
        self.assertEqual(
            [member.key.session_id for member in grouped_extrema[0].members],
            ["run_a", "run_b"],
        )

    def test_group_aligned_extrema_keeps_peak_and_valley_separate(self) -> None:
        grouped_extrema = group_aligned_extrema(
            (
                build_detected_extremum("run_a", aligned_seconds=10.0, kind="peak", sample_index=10),
                build_detected_extremum("run_b", aligned_seconds=10.1, kind="valley", sample_index=11),
            ),
            alignment_tolerance_seconds=0.5,
            reference_session_id="run_a",
        )

        self.assertEqual([group.kind for group in grouped_extrema], ["peak", "valley"])
        self.assertEqual(len(grouped_extrema), 2)

    def test_group_aligned_extrema_keeps_highest_prominence_when_same_series_repeats(self) -> None:
        grouped_extrema = group_aligned_extrema(
            (
                build_detected_extremum("run_a", aligned_seconds=9.9, prominence=1.0, sample_index=10),
                build_detected_extremum("run_a", aligned_seconds=10.1, prominence=3.0, sample_index=11),
                build_detected_extremum("run_b", aligned_seconds=10.0, prominence=2.0, sample_index=20),
            ),
            alignment_tolerance_seconds=0.5,
        )

        self.assertEqual(len(grouped_extrema), 1)
        self.assertEqual(
            [member.sample_index for member in grouped_extrema[0].members],
            [11, 20],
        )

    def test_detected_extrema_group_after_offset_alignment(self) -> None:
        sessions = (
            build_extrema_session("run_a", "RunA", [0.0, 5.0, 0.0, 0.0], is_reference=True),
            build_extrema_session("run_b", "RunB", [0.0, 0.0, 5.0, 0.0], offset_seconds=-1.0),
        )
        config = ExtremaDetectionConfig(
            enabled=True,
            source_series_keys=(SeriesKey("run_a", 2), SeriesKey("run_b", 2)),
            mode="peak",
            min_distance_seconds=1.0,
            min_prominence=1.0,
            alignment_tolerance_seconds=0.25,
        )

        detected_extrema = detect_extrema_for_sessions(sessions, config)
        grouped_extrema = group_aligned_extrema(
            detected_extrema,
            alignment_tolerance_seconds=config.alignment_tolerance_seconds,
            reference_session_id="run_a",
        )

        self.assertEqual(len(detected_extrema), 2)
        self.assertEqual([extremum.aligned_seconds for extremum in detected_extrema], [1.0, 1.0])
        self.assertEqual(len(grouped_extrema), 1)
        self.assertEqual(grouped_extrema[0].anchor_seconds, 1.0)

    def test_build_assigned_curve_points_supports_independent_values_per_file(self) -> None:
        groups = (
            AlignedExtremaGroup(
                group_id="peak-001",
                kind="peak",
                anchor_seconds=5.0,
                members=(
                    build_detected_extremum("run_a", aligned_seconds=5.0, sample_index=10),
                    build_detected_extremum("run_b", aligned_seconds=5.1, sample_index=20),
                ),
            ),
        )

        curve_points = build_assigned_curve_points(
            groups,
            {
                ExtremaPointKey("peak-001", SeriesKey("run_a", 2)): 10.0,
                ExtremaPointKey("peak-001", SeriesKey("run_b", 2)): 20.0,
            },
        )

        self.assertEqual(curve_points[SeriesKey("run_a", 2)], ((5.0,), (10.0,)))
        self.assertEqual(curve_points[SeriesKey("run_b", 2)], ((5.0,), (20.0,)))

    def test_build_assigned_curve_points_sorts_by_anchor_seconds(self) -> None:
        groups = (
            AlignedExtremaGroup(
                group_id="peak-002",
                kind="peak",
                anchor_seconds=8.0,
                members=(build_detected_extremum("run_a", aligned_seconds=8.0, sample_index=80),),
            ),
            AlignedExtremaGroup(
                group_id="peak-001",
                kind="peak",
                anchor_seconds=3.0,
                members=(build_detected_extremum("run_a", aligned_seconds=3.0, sample_index=30),),
            ),
        )

        curve_points = build_assigned_curve_points(
            groups,
            (
                ExtremaAssignment(ExtremaPointKey("peak-001", SeriesKey("run_a", 2)), 1.0),
                ExtremaAssignment(ExtremaPointKey("peak-002", SeriesKey("run_a", 2)), 2.0),
            ),
        )

        self.assertEqual(curve_points[SeriesKey("run_a", 2)][0], (3.0, 8.0))
        self.assertEqual(curve_points[SeriesKey("run_a", 2)][1], (1.0, 2.0))

    def test_build_assigned_curve_points_uses_gap_for_missing_assignment(self) -> None:
        groups = (
            AlignedExtremaGroup(
                group_id="peak-001",
                kind="peak",
                anchor_seconds=1.0,
                members=(build_detected_extremum("run_a", aligned_seconds=1.0, sample_index=10),),
            ),
            AlignedExtremaGroup(
                group_id="peak-002",
                kind="peak",
                anchor_seconds=2.0,
                members=(build_detected_extremum("run_a", aligned_seconds=2.0, sample_index=20),),
            ),
            AlignedExtremaGroup(
                group_id="peak-003",
                kind="peak",
                anchor_seconds=3.0,
                members=(build_detected_extremum("run_a", aligned_seconds=3.0, sample_index=30),),
            ),
        )

        curve_points = build_assigned_curve_points(
            groups,
            {
                ExtremaPointKey("peak-001", SeriesKey("run_a", 2)): 10.0,
                ExtremaPointKey("peak-003", SeriesKey("run_a", 2)): 30.0,
            },
        )
        x_values, y_values = curve_points[SeriesKey("run_a", 2)]

        self.assertEqual(x_values, (1.0, 2.0, 3.0))
        self.assertEqual(y_values[0], 10.0)
        self.assertTrue(math.isnan(y_values[1]))
        self.assertEqual(y_values[2], 30.0)
        self.assertNotIn(0.0, [value for value in y_values if not math.isnan(value)])

    def test_build_comparison_figure_renders_extrema_markers(self) -> None:
        session = build_extrema_session(
            "run_a",
            "RunA",
            [0.0, 4.0, 0.0, 5.0, 0.0],
            is_reference=True,
        )
        config = ExtremaDetectionConfig(
            enabled=True,
            source_series_keys=(SeriesKey("run_a", 2),),
            mode="peak",
            min_distance_seconds=1.0,
            min_prominence=1.0,
        )

        figure = build_comparison_figure(
            (session,),
            [SeriesKey("run_a", 2)],
            width_px=1280,
            height_px=720,
            dpi=120,
            extrema_config=config,
        )
        axis = figure.axes[0]
        marker_offsets = [
            tuple(collection.get_offsets()[0])
            for collection in axis.collections
        ]

        self.assertEqual(marker_offsets, [(1.0, 4.0), (3.0, 5.0)])

    def test_build_comparison_figure_draws_assigned_curve_on_secondary_axis(self) -> None:
        session = build_extrema_session(
            "run_a",
            "RunA",
            [0.0, 4.0, 0.0, 5.0, 0.0],
            is_reference=True,
        )
        config = ExtremaDetectionConfig(
            enabled=True,
            source_series_keys=(SeriesKey("run_a", 2),),
            mode="peak",
            min_distance_seconds=1.0,
            min_prominence=1.0,
            use_secondary_axis=True,
        )
        groups = group_aligned_extrema(
            detect_extrema_for_sessions((session,), config),
            alignment_tolerance_seconds=config.alignment_tolerance_seconds,
            reference_session_id="run_a",
        )

        figure = build_comparison_figure(
            (session,),
            [SeriesKey("run_a", 2)],
            width_px=1280,
            height_px=720,
            dpi=120,
            extrema_config=config,
            extrema_assignments={
                ExtremaPointKey(groups[0].group_id, SeriesKey("run_a", 2)): 10.0,
                ExtremaPointKey(groups[1].group_id, SeriesKey("run_a", 2)): 20.0,
            },
        )

        self.assertEqual(len(figure.axes), 2)
        primary_axis, secondary_axis = figure.axes
        self.assertEqual(len(primary_axis.lines), 1)
        self.assertEqual(secondary_axis.get_ylabel(), "赋值曲线")
        self.assertEqual(len(secondary_axis.lines), 1)
        self.assertEqual(list(secondary_axis.lines[0].get_xdata()), [1.0, 3.0])
        self.assertEqual(list(secondary_axis.lines[0].get_ydata()), [10.0, 20.0])

    def test_build_comparison_figure_supports_independent_assigned_curves_per_file(self) -> None:
        sessions = (
            build_extrema_session("run_a", "RunA", [0.0, 4.0, 0.0, 5.0, 0.0], is_reference=True),
            build_extrema_session("run_b", "RunB", [0.0, 4.0, 0.0, 5.0, 0.0]),
        )
        config = ExtremaDetectionConfig(
            enabled=True,
            source_series_keys=(SeriesKey("run_a", 2), SeriesKey("run_b", 2)),
            mode="peak",
            min_distance_seconds=1.0,
            min_prominence=1.0,
        )
        groups = group_aligned_extrema(
            detect_extrema_for_sessions(sessions, config),
            alignment_tolerance_seconds=config.alignment_tolerance_seconds,
            reference_session_id="run_a",
        )

        assignments = {
            ExtremaPointKey(groups[0].group_id, SeriesKey("run_a", 2)): 10.0,
            ExtremaPointKey(groups[0].group_id, SeriesKey("run_b", 2)): 20.0,
            ExtremaPointKey(groups[1].group_id, SeriesKey("run_a", 2)): 30.0,
            ExtremaPointKey(groups[1].group_id, SeriesKey("run_b", 2)): 40.0,
        }
        figure = build_comparison_figure(
            sessions,
            [SeriesKey("run_a", 2), SeriesKey("run_b", 2)],
            width_px=1280,
            height_px=720,
            dpi=120,
            extrema_config=config,
            extrema_assignments=assignments,
        )
        secondary_axis = figure.axes[1]
        line_by_label = {
            line.get_label(): list(line.get_ydata())
            for line in secondary_axis.lines
        }

        self.assertEqual(line_by_label["RunA · 赋值曲线"], [10.0, 30.0])
        self.assertEqual(line_by_label["RunB · 赋值曲线"], [20.0, 40.0])

    def test_build_comparison_figure_point_color_override_beats_series_color(self) -> None:
        session = build_extrema_session(
            "run_a",
            "RunA",
            [0.0, 4.0, 0.0],
            is_reference=True,
        )
        config = ExtremaDetectionConfig(
            enabled=True,
            source_series_keys=(SeriesKey("run_a", 2),),
            mode="peak",
            min_distance_seconds=1.0,
            min_prominence=1.0,
        )
        groups = group_aligned_extrema(
            detect_extrema_for_sessions((session,), config),
            alignment_tolerance_seconds=config.alignment_tolerance_seconds,
            reference_session_id="run_a",
        )
        point_key = ExtremaPointKey(groups[0].group_id, SeriesKey("run_a", 2))

        figure = build_comparison_figure(
            (session,),
            [SeriesKey("run_a", 2)],
            width_px=1280,
            height_px=720,
            dpi=120,
            color_by_series={SeriesKey("run_a", 2): "#ff0000"},
            extrema_config=config,
            extrema_point_colors={point_key: "#00ff00"},
        )
        axis = figure.axes[0]

        self.assertEqual(axis.lines[0].get_color(), "#ff0000")
        self.assertEqual(tuple(axis.collections[0].get_facecolors()[0]), to_rgba("#00ff00"))


class CoreSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        cls.fixture_path = write_synthetic_hwinfo_csv(Path(cls.temp_dir.name) / "synthetic_hwinfo.csv")
        cls.data = load_hwinfo_csv(cls.fixture_path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def test_loads_synthetic_csv_fixture(self) -> None:
        self.assertGreater(len(self.data.timestamps), 0)
        self.assertEqual(len(self.data.columns), 4)
        self.assertEqual(self.data.source_path.name, "synthetic_hwinfo.csv")

    def test_loads_synthetic_csv_with_preloaded_series(self) -> None:
        data = load_hwinfo_csv(self.fixture_path, preload_numeric=True)

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
        self.assertIsNone(axis.get_legend())
    def test_compact_time_formatter_omits_zero_hour_component(self) -> None:
        self.assertEqual(format_compact_elapsed_time(65), "01:05")
        self.assertEqual(format_compact_elapsed_time(3605), "01:00:05")
        self.assertEqual(format_compact_elapsed_time(-65), "-01:05")

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

    def test_build_figure_applies_axis_grid_and_tick_text_colors(self) -> None:
        column_index = next(
            column.index
            for column in self.data.columns
            if len(self.data.extract_series(column.index)[1]) > 10
        )

        figure = build_figure(
            self.data,
            [column_index],
            width_px=1280,
            height_px=720,
            dpi=120,
            style=ChartStyle(
                axis_color="#112233",
                grid_color="#223344",
                time_text_color="#334455",
                value_text_color="#445566",
            ),
        )
        axis = figure.axes[0]
        x_tick = axis.xaxis.get_major_ticks()[0]
        y_tick = axis.yaxis.get_major_ticks()[0]

        self.assertEqual(axis.spines["bottom"].get_edgecolor(), to_rgba("#112233"))
        self.assertEqual(axis.spines["left"].get_edgecolor(), to_rgba("#112233"))
        self.assertEqual(x_tick.tick1line.get_color(), "#112233")
        self.assertEqual(y_tick.tick1line.get_color(), "#112233")
        self.assertEqual(x_tick.label1.get_color(), "#334455")
        self.assertEqual(y_tick.label1.get_color(), "#445566")
        self.assertEqual(axis.get_xgridlines()[0].get_color(), "#223344")
        self.assertEqual(axis.get_ygridlines()[0].get_color(), "#223344")

    def test_build_figure_applies_legend_text_color_and_font_family(self) -> None:
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
                title="字体测试",
                font_family="DejaVu Sans",
                legend_text_color="#abcdef",
            ),
        )
        axis = figure.axes[0]
        legend = axis.get_legend()

        self.assertIsNotNone(legend)
        self.assertEqual(axis.title.get_fontfamily(), ["DejaVu Sans"])
        self.assertEqual(axis.xaxis.get_major_ticks()[0].label1.get_fontfamily(), ["DejaVu Sans"])
        self.assertTrue(legend.get_texts())
        for legend_text in legend.get_texts():
            self.assertEqual(legend_text.get_color(), "#abcdef")
            self.assertEqual(legend_text.get_fontfamily(), ["DejaVu Sans"])

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

    def test_build_figure_curve_only_mode_keeps_only_data_lines(self) -> None:
        base_time = datetime(2026, 4, 13, 12, 0, 0)
        timestamps = [base_time + timedelta(seconds=index) for index in range(10)]
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
                ["13/04/2026", f"12:00:{index:02d}", f"{float(index):.1f}", f"{float(index + 1):.1f}"]
                for index in range(10)
            ],
        )

        figure = build_figure(
            data,
            [2, 3],
            width_px=1280,
            height_px=720,
            dpi=120,
            style=ChartStyle(
                title="不应显示",
                curve_only_mode=True,
                show_grid=True,
                show_legend=True,
                show_time_axis=True,
                show_value_axis=True,
            ),
        )
        axis = figure.axes[0]

        self.assertEqual(len(axis.lines), 2)
        self.assertEqual(axis.get_title(), "")
        self.assertIsNone(axis.get_legend())
        self.assertTrue(all(not spine.get_visible() for spine in axis.spines.values()))
        self.assertTrue(all(not gridline.get_visible() for gridline in axis.get_xgridlines()))
        self.assertTrue(all(not gridline.get_visible() for gridline in axis.get_ygridlines()))
        self.assertFalse(axis.xaxis.get_major_ticks()[0].tick1line.get_visible())
        self.assertFalse(axis.xaxis.get_major_ticks()[0].label1.get_visible())
        self.assertFalse(axis.yaxis.get_major_ticks()[0].tick1line.get_visible())
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

    def test_compute_global_time_bounds_supports_negative_offsets(self) -> None:
        sessions = (
            build_loaded_session("run_a", "RunA", is_reference=True),
            build_loaded_session("run_b", "RunB", offset_seconds=-5.0),
        )

        self.assertEqual(compute_global_time_bounds(sessions), (-5.0, 3.0))

    def test_default_source_trim_matches_full_session_range(self) -> None:
        session = build_loaded_session("run_a", "RunA", offset_seconds=5.0)

        self.assertEqual(resolve_session_source_trim_range(session), (0.0, 3.0))
        self.assertEqual(compute_session_timeline_range(session), (5.0, 8.0))
        self.assertEqual(compute_session_active_timeline_range(session), (5.0, 8.0))

    def test_filter_visible_series_applies_source_trim_before_offset(self) -> None:
        session = build_loaded_session(
            "run_a",
            "RunA",
            offset_seconds=5.0,
            source_trim_start_seconds=1.0,
            source_trim_end_seconds=2.0,
        )

        visible_series = filter_visible_series((session,), [SeriesKey("run_a", 2)])

        self.assertEqual(len(visible_series), 1)
        self.assertEqual(list(visible_series[0].x_values), [6.0, 7.0])
        self.assertEqual(list(visible_series[0].y_values), [2.0, 3.0])

    def test_source_trim_and_global_work_area_both_apply_to_comparison_series(self) -> None:
        session = build_loaded_session(
            "run_a",
            "RunA",
            offset_seconds=5.0,
            source_trim_start_seconds=1.0,
            source_trim_end_seconds=3.0,
        )

        visible_series = filter_visible_series(
            (session,),
            [SeriesKey("run_a", 2)],
            visible_range_seconds=(7.0, 8.0),
        )

        self.assertEqual(len(visible_series), 1)
        self.assertEqual(list(visible_series[0].x_values), [7.0, 8.0])
        self.assertEqual(list(visible_series[0].y_values), [3.0, 4.0])

    def test_global_time_bounds_use_active_trim_ranges(self) -> None:
        sessions = (
            build_loaded_session(
                "run_a",
                "RunA",
                is_reference=True,
                source_trim_start_seconds=1.0,
                source_trim_end_seconds=2.0,
            ),
            build_loaded_session(
                "run_b",
                "RunB",
                offset_seconds=10.0,
                source_trim_start_seconds=2.0,
                source_trim_end_seconds=3.0,
            ),
        )

        self.assertEqual(compute_global_time_bounds(sessions), (1.0, 13.0))

    def test_normalize_offsets_for_reference_preserves_relative_layout(self) -> None:
        sessions = (
            build_loaded_session("run_a", "RunA", is_reference=True),
            build_loaded_session("run_b", "RunB", offset_seconds=8.0),
            build_loaded_session("run_c", "RunC", offset_seconds=-3.0),
        )

        normalized_sessions = normalize_offsets_for_reference(sessions, "run_b")

        self.assertEqual([session.offset_seconds for session in normalized_sessions], [-8.0, 0.0, -11.0])
        self.assertEqual([session.is_reference for session in normalized_sessions], [False, True, False])

    def test_normalize_offsets_for_reference_preserves_source_trim_ranges(self) -> None:
        sessions = (
            build_loaded_session("run_a", "RunA", is_reference=True),
            build_loaded_session(
                "run_b",
                "RunB",
                offset_seconds=8.0,
                source_trim_start_seconds=1.0,
                source_trim_end_seconds=2.0,
            ),
        )

        normalized_sessions = normalize_offsets_for_reference(sessions, "run_b")

        self.assertEqual(normalized_sessions[1].source_trim_start_seconds, 1.0)
        self.assertEqual(normalized_sessions[1].source_trim_end_seconds, 2.0)

    def test_build_comparison_figure_offsets_each_session_x_axis(self) -> None:
        sessions = (
            build_loaded_session("run_a", "RunA", is_reference=True),
            build_loaded_session("run_b", "RunB", offset_seconds=5.0),
        )

        figure = build_comparison_figure(
            sessions,
            [SeriesKey("run_a", 2), SeriesKey("run_b", 2)],
            width_px=1280,
            height_px=720,
            dpi=120,
        )
        axis = figure.axes[0]

        self.assertEqual(list(axis.lines[0].get_xdata()), [0.0, 1.0, 2.0, 3.0])
        self.assertEqual(list(axis.lines[1].get_xdata()), [5.0, 6.0, 7.0, 8.0])

    def test_build_comparison_figure_uses_series_key_colors_without_collision(self) -> None:
        sessions = (
            build_loaded_session("run_a", "RunA", is_reference=True),
            build_loaded_session("run_b", "RunB"),
        )

        figure = build_comparison_figure(
            sessions,
            [SeriesKey("run_a", 2), SeriesKey("run_b", 2)],
            width_px=1280,
            height_px=720,
            dpi=120,
            color_by_series={
                SeriesKey("run_a", 2): "#ff0000",
                SeriesKey("run_b", 2): "#00ff00",
            },
        )
        axis = figure.axes[0]

        self.assertEqual(axis.lines[0].get_color(), "#ff0000")
        self.assertEqual(axis.lines[1].get_color(), "#00ff00")

    def test_build_comparison_figure_uses_alias_in_legend_for_same_column_name(self) -> None:
        sessions = (
            build_loaded_session("run_a", "RunA", is_reference=True),
            build_loaded_session("run_b", "RunB"),
        )

        figure = build_comparison_figure(
            sessions,
            [SeriesKey("run_a", 2), SeriesKey("run_b", 2)],
            width_px=1280,
            height_px=720,
            dpi=120,
        )
        axis = figure.axes[0]
        legend = axis.get_legend()

        self.assertIsNotNone(legend)
        self.assertEqual(
            [legend_text.get_text() for legend_text in legend.get_texts()],
            ["RunA · [002] CPU", "RunB · [002] CPU"],
        )

    def test_build_comparison_figure_trims_global_visible_range(self) -> None:
        sessions = (
            build_loaded_session("run_a", "RunA", is_reference=True),
            build_loaded_session("run_b", "RunB", offset_seconds=5.0),
        )

        figure = build_comparison_figure(
            sessions,
            [SeriesKey("run_a", 2), SeriesKey("run_b", 2)],
            width_px=1280,
            height_px=720,
            dpi=120,
            visible_range_seconds=(2.0, 6.0),
        )
        axis = figure.axes[0]

        self.assertEqual(list(axis.lines[0].get_xdata()), [2.0, 3.0])
        self.assertEqual(list(axis.lines[1].get_xdata()), [5.0, 6.0])
        self.assertEqual(axis.get_xlim(), (2.0, 6.0))

    def test_build_comparison_output_name_uses_aliases_and_series_names(self) -> None:
        sessions = (
            build_loaded_session("run_a", "RunA", is_reference=True),
            build_loaded_session("run_b", "RunB"),
        )

        output_name = build_comparison_output_name(
            sessions,
            [SeriesKey("run_a", 2), SeriesKey("run_b", 3)],
        )

        self.assertEqual(output_name, "compare__RunA__RunB__CPU_GPU.png")


class RuntimeLoggingTests(unittest.TestCase):
    def test_load_hwinfo_csv_writes_runtime_log_entry(self) -> None:
        output_root = ROOT / "_test_output" / "test_core_runtime_logging"
        if output_root.exists():
            shutil.rmtree(output_root)
        output_root.mkdir(parents=True, exist_ok=True)

        fixture_path = write_synthetic_hwinfo_csv(output_root / "synthetic_hwinfo.csv")
        log_path = configure_runtime_logging(output_root / "logs", force_reconfigure=True)
        try:
            load_hwinfo_csv(fixture_path)
        finally:
            shutdown_runtime_logging()

        log_text = log_path.read_text(encoding="utf-8")

        self.assertIn("Loading CSV file", log_text)
        self.assertIn("Loaded CSV file", log_text)
        self.assertIn("synthetic_hwinfo.csv", log_text)
        self.assertIn("rows=24", log_text)


if __name__ == "__main__":
    unittest.main()
