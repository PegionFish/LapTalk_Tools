from __future__ import annotations

import logging
import math
import re
import io
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Mapping, Sequence

from matplotlib import font_manager, rcParams
from matplotlib.colors import is_color_like
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, MultipleLocator

from .csv_log import (
    CsvLogData,
    HWiNFOData,
    SensorColumn,
    build_elapsed_seconds,
    build_sensor_columns,
    choose_best_decoding,
    count_cjk_characters,
    decode_csv_bytes,
    detect_bom_encoding,
    find_header_index,
    is_cjk_character,
    load_hwinfo_csv,
    normalize_row_length,
    parse_numeric_value,
    parse_timestamp,
    score_decoded_text,
)

try:
    from scipy.signal import find_peaks as scipy_find_peaks
except ImportError:
    scipy_find_peaks = None

FONT_CANDIDATES = (
    "Microsoft YaHei",
    "Microsoft JhengHei",
    "DengXian",
    "SimHei",
    "SimSun",
    "Noto Sans CJK SC",
    "PingFang SC",
    "Arial Unicode MS",
    "Segoe UI",
)
LEGEND_LOCATIONS = {
    "best",
    "upper right",
    "upper left",
    "lower right",
    "lower left",
    "upper center",
    "lower center",
    "center left",
    "center right",
    "center",
}
MIN_TIME_TICK_DENSITY = 1
MAX_TIME_TICK_DENSITY = 12
DEFAULT_TIME_TICK_DENSITY = 7
EXTREMA_KINDS = ("peak", "valley")
EXTREMA_MODES = (*EXTREMA_KINDS, "both")

_FONT_READY = False
logger = logging.getLogger("csv_visual.core")


@dataclass(frozen=True)
class ChartStyle:
    title: str | None = None
    line_width: float = 1.8
    curve_only_mode: bool = False
    show_grid: bool = True
    grid_alpha: float = 0.28
    grid_color: str | None = None
    show_legend: bool = True
    show_time_axis: bool = True
    show_value_axis: bool = True
    axis_color: str | None = None
    time_text_color: str | None = None
    value_text_color: str | None = None
    legend_location: str = "best"
    time_tick_density: int = DEFAULT_TIME_TICK_DENSITY
    fixed_time_interval_seconds: int | None = None
    legend_text_color: str | None = None
    font_family: str | None = None


@dataclass(frozen=True)
class SeriesKey:
    session_id: str
    column_index: int


@dataclass(frozen=True)
class SeriesDescriptor:
    key: SeriesKey
    session_alias: str
    column_display_name: str
    legend_label: str


@dataclass(frozen=True)
class VisibleSeries:
    descriptor: SeriesDescriptor
    x_values: tuple[float, ...]
    y_values: tuple[float, ...]


@dataclass(frozen=True)
class ExtremaDetectionConfig:
    enabled: bool = False
    source_series_keys: tuple[SeriesKey, ...] = ()
    mode: str = "both"
    min_distance_seconds: float = 1.0
    min_prominence: float = 0.0
    smoothing_window: int = 1
    alignment_tolerance_seconds: float = 1.0
    use_secondary_axis: bool = True


@dataclass(frozen=True)
class DetectedExtremum:
    event_id: str
    key: SeriesKey
    kind: str
    source_seconds: float
    aligned_seconds: float
    source_value: float
    prominence: float
    sample_index: int


@dataclass(frozen=True)
class AlignedExtremaGroup:
    group_id: str
    kind: str
    anchor_seconds: float
    members: tuple[DetectedExtremum, ...]


@dataclass(frozen=True)
class ExtremaPointKey:
    group_id: str
    key: SeriesKey


@dataclass(frozen=True)
class ExtremaAssignment:
    point_key: ExtremaPointKey
    assigned_value: float | None = None


@dataclass(frozen=True)
class LoadedCsvSession:
    session_id: str
    alias: str
    data: CsvLogData
    offset_seconds: float = 0.0
    is_reference: bool = False
    is_visible: bool = True
    preload_ready: bool = False
    preload_error: str | None = None
    source_trim_start_seconds: float = 0.0
    source_trim_end_seconds: float | None = None


def build_series_descriptors(sessions: Sequence[LoadedCsvSession]) -> list[SeriesDescriptor]:
    descriptors: list[SeriesDescriptor] = []
    for session in sessions:
        if not session.is_visible:
            continue

        session_alias = session.alias.strip() or session.data.source_path.stem
        for column in session.data.columns:
            column_display_name = column.display_name
            descriptors.append(
                SeriesDescriptor(
                    key=SeriesKey(session_id=session.session_id, column_index=column.index),
                    session_alias=session_alias,
                    column_display_name=column_display_name,
                    legend_label=f"{session_alias} · {column_display_name}",
                )
            )

    return descriptors


def align_series_x_values(x_values: Sequence[float], offset_seconds: float) -> list[float]:
    resolved_offset = float(offset_seconds)
    return [float(x_value) + resolved_offset for x_value in x_values]


def get_session_source_duration(session: LoadedCsvSession) -> float:
    if not session.data.elapsed_seconds:
        return 0.0

    return max(0.0, float(session.data.elapsed_seconds[-1]) - float(session.data.elapsed_seconds[0]))


def resolve_session_source_trim_range(session: LoadedCsvSession) -> tuple[float, float]:
    if not session.data.elapsed_seconds:
        return 0.0, 0.0

    source_start_seconds = float(session.data.elapsed_seconds[0])
    source_end_seconds = float(session.data.elapsed_seconds[-1])
    trim_start_seconds = max(
        source_start_seconds,
        min(float(session.source_trim_start_seconds), source_end_seconds),
    )
    if session.source_trim_end_seconds is None:
        trim_end_seconds = source_end_seconds
    else:
        trim_end_seconds = max(
            source_start_seconds,
            min(float(session.source_trim_end_seconds), source_end_seconds),
        )
    if trim_end_seconds < trim_start_seconds:
        trim_end_seconds = trim_start_seconds
    return float(trim_start_seconds), float(trim_end_seconds)


def compute_session_timeline_range(session: LoadedCsvSession) -> tuple[float, float]:
    if not session.data.elapsed_seconds:
        start_seconds = float(session.offset_seconds)
        return start_seconds, start_seconds + 1.0

    offset_seconds = float(session.offset_seconds)
    return (
        float(session.data.elapsed_seconds[0]) + offset_seconds,
        float(session.data.elapsed_seconds[-1]) + offset_seconds,
    )


def compute_session_active_timeline_range(session: LoadedCsvSession) -> tuple[float, float]:
    trim_start_seconds, trim_end_seconds = resolve_session_source_trim_range(session)
    offset_seconds = float(session.offset_seconds)
    return trim_start_seconds + offset_seconds, trim_end_seconds + offset_seconds


def compute_global_time_bounds(sessions: Sequence[LoadedCsvSession]) -> tuple[float, float]:
    visible_sessions = [session for session in sessions if session.is_visible and session.data.elapsed_seconds]
    if not visible_sessions:
        return 0.0, 1.0

    active_ranges = [compute_session_active_timeline_range(session) for session in visible_sessions]
    start_seconds = 0.0
    end_seconds = max(active_end_seconds for _, active_end_seconds in active_ranges)
    if end_seconds <= start_seconds:
        end_seconds = start_seconds + 1.0
    return float(start_seconds), float(end_seconds)


def summarize_loaded_sessions_for_log(sessions: Sequence[LoadedCsvSession], *, limit: int = 6) -> str:
    if not sessions:
        return "-"

    parts: list[str] = []
    for session in sessions[:limit]:
        trim_start_seconds, trim_end_seconds = resolve_session_source_trim_range(session)
        session_alias = session.alias.strip() or session.data.source_path.stem
        parts.append(
            (
                f"{session_alias}(id={session.session_id},file={session.data.source_path.name},"
                f"reference={session.is_reference},visible={session.is_visible},"
                f"offset={float(session.offset_seconds):.3f},"
                f"trim={trim_start_seconds:.3f}->{trim_end_seconds:.3f},"
                f"preload_ready={session.preload_ready})"
            )
        )

    if len(sessions) > limit:
        parts.append(f"...(+{len(sessions) - limit} more)")
    return "; ".join(parts)


def normalize_offsets_for_reference(
    sessions: Sequence[LoadedCsvSession],
    reference_session_id: str,
) -> tuple[LoadedCsvSession, ...]:
    if not sessions:
        return ()

    reference_session = next(
        (session for session in sessions if session.session_id == reference_session_id),
        None,
    )
    if reference_session is None:
        raise KeyError(f"未找到基准文件会话：{reference_session_id}")

    reference_offset = float(reference_session.offset_seconds)
    return tuple(
        replace(
            session,
            offset_seconds=float(session.offset_seconds) - reference_offset,
            is_reference=session.session_id == reference_session_id,
        )
        for session in sessions
    )


def resolve_comparison_visible_range_seconds(
    sessions: Sequence[LoadedCsvSession],
    visible_range_seconds: tuple[float, float] | None,
) -> tuple[float, float]:
    global_start_seconds, global_end_seconds = compute_global_time_bounds(sessions)
    if visible_range_seconds is None:
        return global_start_seconds, global_end_seconds

    requested_start_seconds, requested_end_seconds = visible_range_seconds
    start_seconds = max(global_start_seconds, float(requested_start_seconds))
    end_seconds = min(global_end_seconds, float(requested_end_seconds))
    if end_seconds <= start_seconds:
        end_seconds = start_seconds + 1.0
    return float(start_seconds), float(end_seconds)


def filter_visible_series(
    sessions: Sequence[LoadedCsvSession],
    selected_series: Sequence[SeriesKey],
    visible_range_seconds: tuple[float, float] | None = None,
) -> list[VisibleSeries]:
    visible_sessions = tuple(session for session in sessions if session.is_visible)
    session_by_id = {session.session_id: session for session in visible_sessions}
    descriptor_by_key = {descriptor.key: descriptor for descriptor in build_series_descriptors(visible_sessions)}
    visible_start_seconds, visible_end_seconds = resolve_comparison_visible_range_seconds(
        visible_sessions,
        visible_range_seconds,
    )

    visible_series: list[VisibleSeries] = []
    for series_key in selected_series:
        session = session_by_id.get(series_key.session_id)
        descriptor = descriptor_by_key.get(series_key)
        if session is None or descriptor is None:
            continue

        x_values, y_values = session.data.extract_series(series_key.column_index)
        if not y_values:
            continue

        source_start_seconds, source_end_seconds = resolve_session_source_trim_range(session)
        source_trimmed_x_values, source_trimmed_y_values = trim_series_to_range(
            x_values,
            y_values,
            source_start_seconds,
            source_end_seconds,
        )
        if not source_trimmed_y_values:
            continue

        aligned_x_values = align_series_x_values(source_trimmed_x_values, session.offset_seconds)
        trimmed_x_values, trimmed_y_values = trim_series_to_range(
            aligned_x_values,
            source_trimmed_y_values,
            visible_start_seconds,
            visible_end_seconds,
        )
        if not trimmed_y_values:
            continue

        visible_series.append(
            VisibleSeries(
                descriptor=descriptor,
                x_values=tuple(trimmed_x_values),
                y_values=tuple(trimmed_y_values),
            )
        )

    logger.debug(
        (
            "Filtered visible series visible_sessions=%d selected_series=%d "
            "result_series=%d visible_range=(%.3f, %.3f)"
        ),
        len(visible_sessions),
        len(selected_series),
        len(visible_series),
        visible_start_seconds,
        visible_end_seconds,
    )
    return visible_series


def detect_series_extrema(
    key: SeriesKey,
    source_seconds: Sequence[float],
    source_values: Sequence[float],
    *,
    sample_indices: Sequence[int] | None = None,
    offset_seconds: float = 0.0,
    mode: str = "both",
    min_distance_seconds: float = 1.0,
    min_prominence: float = 0.0,
    smoothing_window: int = 1,
) -> tuple[DetectedExtremum, ...]:
    resolved_mode = normalize_extrema_mode(mode)
    if len(source_seconds) != len(source_values):
        raise ValueError("时间序列长度与数值序列长度不一致。")
    if sample_indices is not None and len(sample_indices) != len(source_values):
        raise ValueError("采样索引数量与数值序列长度不一致。")
    if min_distance_seconds < 0:
        raise ValueError("极值最小间距不能为负数。")
    if min_prominence < 0:
        raise ValueError("极值最小突出度不能为负数。")
    if smoothing_window < 1:
        raise ValueError("平滑窗口必须大于等于 1。")
    if len(source_values) < 3:
        return ()
    if scipy_find_peaks is None:
        raise RuntimeError(
            "未安装 scipy，无法进行峰谷检测。请先在 csv_visual/ 下运行 "
            "`pip install -r requirements.txt`。"
        )

    resolved_sample_indices = (
        tuple(int(index) for index in sample_indices)
        if sample_indices is not None
        else tuple(range(len(source_values)))
    )
    smoothed_values = smooth_series_values(source_values, smoothing_window)
    peak_kwargs: dict[str, object] = {
        "prominence": float(min_prominence),
    }
    distance_samples = resolve_distance_samples(source_seconds, min_distance_seconds)
    if distance_samples is not None:
        peak_kwargs["distance"] = distance_samples

    detected_extrema: list[DetectedExtremum] = []
    if resolved_mode in {"peak", "both"}:
        peak_indices, peak_properties = scipy_find_peaks(smoothed_values, **peak_kwargs)
        detected_extrema.extend(
            build_detected_extrema(
                key,
                kind="peak",
                source_seconds=source_seconds,
                source_values=source_values,
                sample_indices=resolved_sample_indices,
                offset_seconds=offset_seconds,
                peak_indices=peak_indices,
                peak_properties=peak_properties,
            )
        )
    if resolved_mode in {"valley", "both"}:
        valley_indices, valley_properties = scipy_find_peaks(
            [-float(value) for value in smoothed_values],
            **peak_kwargs,
        )
        detected_extrema.extend(
            build_detected_extrema(
                key,
                kind="valley",
                source_seconds=source_seconds,
                source_values=source_values,
                sample_indices=resolved_sample_indices,
                offset_seconds=offset_seconds,
                peak_indices=valley_indices,
                peak_properties=valley_properties,
            )
        )

    return tuple(
        sorted(
            detected_extrema,
            key=lambda item: (
                float(item.source_seconds),
                0 if item.kind == "peak" else 1,
                item.sample_index,
            ),
        )
    )


def detect_extrema_for_sessions(
    sessions: Sequence[LoadedCsvSession],
    config: ExtremaDetectionConfig,
) -> tuple[DetectedExtremum, ...]:
    if not config.enabled or not config.source_series_keys:
        return ()

    visible_sessions = {
        session.session_id: session
        for session in sessions
        if session.is_visible
    }
    detected_extrema: list[DetectedExtremum] = []
    seen_series_keys: set[SeriesKey] = set()
    for series_key in config.source_series_keys:
        if series_key in seen_series_keys:
            continue
        seen_series_keys.add(series_key)

        session = visible_sessions.get(series_key.session_id)
        if session is None:
            continue
        x_values, y_values = session.data.extract_series(series_key.column_index)
        if len(y_values) < 3:
            continue

        trim_start_seconds, trim_end_seconds = resolve_session_source_trim_range(session)
        trimmed_sample_indices, trimmed_x_values, trimmed_y_values = trim_series_with_sample_indices(
            x_values,
            y_values,
            trim_start_seconds,
            trim_end_seconds,
        )
        if len(trimmed_y_values) < 3:
            continue

        detected_extrema.extend(
            detect_series_extrema(
                series_key,
                trimmed_x_values,
                trimmed_y_values,
                sample_indices=trimmed_sample_indices,
                offset_seconds=session.offset_seconds,
                mode=config.mode,
                min_distance_seconds=config.min_distance_seconds,
                min_prominence=config.min_prominence,
                smoothing_window=config.smoothing_window,
            )
        )

    result = tuple(
        sorted(
            detected_extrema,
            key=lambda item: (
                float(item.aligned_seconds),
                0 if item.kind == "peak" else 1,
                item.key.session_id,
                item.key.column_index,
                item.sample_index,
            ),
        )
    )
    logger.debug(
        (
            "Detected extrema sessions=%d source_series=%d mode=%s "
            "result_extrema=%d tolerance=%.3f"
        ),
        len(visible_sessions),
        len(seen_series_keys),
        config.mode,
        len(result),
        float(config.alignment_tolerance_seconds),
    )
    return result


def group_aligned_extrema(
    detected_extrema: Sequence[DetectedExtremum],
    *,
    alignment_tolerance_seconds: float = 1.0,
    reference_session_id: str | None = None,
) -> tuple[AlignedExtremaGroup, ...]:
    if alignment_tolerance_seconds < 0:
        raise ValueError("分组容差不能为负数。")

    grouped_extrema: list[AlignedExtremaGroup] = []
    for kind in EXTREMA_KINDS:
        kind_extrema = sorted(
            (extremum for extremum in detected_extrema if extremum.kind == kind),
            key=lambda item: (
                float(item.aligned_seconds),
                item.key.session_id,
                item.key.column_index,
                item.sample_index,
            ),
        )
        if not kind_extrema:
            continue

        current_members: list[DetectedExtremum] = []
        for extremum in kind_extrema:
            if not current_members:
                current_members = [extremum]
                continue

            anchor_seconds = resolve_extrema_group_anchor(
                current_members,
                reference_session_id=reference_session_id,
            )
            if abs(float(extremum.aligned_seconds) - anchor_seconds) <= float(alignment_tolerance_seconds):
                current_members.append(extremum)
                continue

            grouped_extrema.append(
                finalize_extrema_group(
                    kind,
                    current_members,
                    reference_session_id=reference_session_id,
                )
            )
            current_members = [extremum]

        if current_members:
            grouped_extrema.append(
                finalize_extrema_group(
                    kind,
                    current_members,
                    reference_session_id=reference_session_id,
                )
            )

    ordered_groups = sorted(
        grouped_extrema,
        key=lambda group: (
            float(group.anchor_seconds),
            0 if group.kind == "peak" else 1,
            tuple(
                (
                    member.key.session_id,
                    member.key.column_index,
                    member.sample_index,
                )
                for member in group.members
            ),
        ),
    )
    return tuple(
        replace(group, group_id=f"{group.kind}-{index:03d}")
        for index, group in enumerate(ordered_groups, start=1)
    )


def build_assigned_curve_points(
    groups: Sequence[AlignedExtremaGroup],
    assignments: Mapping[ExtremaPointKey, float | None] | Sequence[ExtremaAssignment],
) -> dict[SeriesKey, tuple[tuple[float, ...], tuple[float, ...]]]:
    assignment_map = normalize_extrema_assignments(assignments)
    ordered_groups = sorted(
        groups,
        key=lambda group: (
            float(group.anchor_seconds),
            0 if group.kind == "peak" else 1,
            group.group_id,
        ),
    )

    curve_points_by_series: dict[SeriesKey, list[tuple[float, float]]] = defaultdict(list)
    for group in ordered_groups:
        for member in sorted(
            group.members,
            key=lambda item: (
                item.key.session_id,
                item.key.column_index,
                item.sample_index,
            ),
        ):
            point_key = ExtremaPointKey(group_id=group.group_id, key=member.key)
            assigned_value = assignment_map.get(point_key)
            curve_points = curve_points_by_series[member.key]
            if assigned_value is None:
                if curve_points and not math.isnan(curve_points[-1][1]):
                    curve_points.append((float(group.anchor_seconds), math.nan))
                continue

            curve_points.append((float(group.anchor_seconds), float(assigned_value)))

    result: dict[SeriesKey, tuple[tuple[float, ...], tuple[float, ...]]] = {}
    for series_key, curve_points in curve_points_by_series.items():
        if curve_points and math.isnan(curve_points[-1][1]):
            curve_points = curve_points[:-1]
        if not curve_points:
            continue

        result[series_key] = (
            tuple(float(x_value) for x_value, _ in curve_points),
            tuple(float(y_value) for _, y_value in curve_points),
        )

    return result


def build_comparison_figure(
    sessions: Sequence[LoadedCsvSession],
    selected_series: Sequence[SeriesKey],
    title: str | None = None,
    width_px: int = 1920,
    height_px: int = 1080,
    dpi: int = 160,
    style: ChartStyle | None = None,
    color_by_series: Mapping[SeriesKey, str] | None = None,
    visible_range_seconds: tuple[float, float] | None = None,
    extrema_config: ExtremaDetectionConfig | None = None,
    extrema_assignments: Mapping[ExtremaPointKey, float | None] | Sequence[ExtremaAssignment] | None = None,
    extrema_point_colors: Mapping[ExtremaPointKey, str] | None = None,
) -> Figure:
    started_at = perf_counter()
    visible_sessions = tuple(session for session in sessions if session.is_visible)
    logger.info(
        (
            "Building comparison figure sessions=%d selected_series=%d "
            "size=%dx%d dpi=%d visible_range=%s extrema_enabled=%s sessions_detail=%s"
        ),
        len(visible_sessions),
        len(selected_series),
        width_px,
        height_px,
        dpi,
        visible_range_seconds if visible_range_seconds is not None else "auto",
        bool(extrema_config is not None and extrema_config.enabled and extrema_config.source_series_keys),
        summarize_loaded_sessions_for_log(visible_sessions),
    )
    if not visible_sessions:
        raise ValueError("请至少加载一个 CSV 文件。")
    if not selected_series:
        raise ValueError("至少需要选择一个参数。")
    if width_px < 200 or height_px < 200:
        raise ValueError("输出尺寸过小，请至少使用 200 x 200。")
    if dpi < 24:
        raise ValueError("DPI 不能小于 24。")

    chart_style = resolve_chart_style(style, title=title)
    if chart_style.line_width <= 0:
        raise ValueError("曲线线宽必须大于 0。")
    if not 0 <= chart_style.grid_alpha <= 1:
        raise ValueError("网格透明度必须在 0 到 1 之间。")
    if chart_style.legend_location not in LEGEND_LOCATIONS:
        raise ValueError(f"不支持的图例位置：{chart_style.legend_location}")
    if not MIN_TIME_TICK_DENSITY <= chart_style.time_tick_density <= MAX_TIME_TICK_DENSITY:
        raise ValueError(
            f"时间刻度密度必须在 {MIN_TIME_TICK_DENSITY} 到 {MAX_TIME_TICK_DENSITY} 之间。"
        )
    if chart_style.fixed_time_interval_seconds is not None and chart_style.fixed_time_interval_seconds <= 0:
        raise ValueError("固定时间刻度间隔必须大于 0。")
    validate_chart_style_colors(chart_style)

    visible_start_seconds, visible_end_seconds = resolve_comparison_visible_range_seconds(
        visible_sessions,
        visible_range_seconds,
    )
    visible_series = filter_visible_series(
        visible_sessions,
        selected_series,
        visible_range_seconds=(visible_start_seconds, visible_end_seconds),
    )
    if not visible_series:
        raise ValueError("所选参数在当前可视范围内没有可用于绘图的数值数据。")

    configure_matplotlib_fonts()

    figure = Figure(
        figsize=(width_px / dpi, height_px / dpi),
        dpi=dpi,
        constrained_layout=True,
    )
    axis = figure.add_subplot(111)
    figure.patch.set_alpha(0.0)
    axis.set_facecolor("none")

    configure_time_axis(axis, visible_start_seconds, visible_end_seconds, chart_style)

    include_session_alias = len(visible_sessions) > 1
    plotted_line_count = 0
    line_color_by_series: dict[SeriesKey, str] = {}
    for series in visible_series:
        line_kwargs: dict[str, object] = {}
        if color_by_series is not None:
            selected_color = color_by_series.get(series.descriptor.key)
            if selected_color:
                line_kwargs["color"] = selected_color

        plotted_line = axis.plot(
            list(series.x_values),
            list(series.y_values),
            linewidth=chart_style.line_width,
            label=(
                series.descriptor.legend_label
                if include_session_alias
                else series.descriptor.column_display_name
            ),
            **line_kwargs,
        )[0]
        line_color_by_series[series.descriptor.key] = str(plotted_line.get_color())
        plotted_line_count += 1

    grouped_extrema: tuple[AlignedExtremaGroup, ...] = ()
    assignment_map: dict[ExtremaPointKey, float | None] = normalize_extrema_assignments(
        {} if extrema_assignments is None else extrema_assignments
    )
    fallback_order_by_series: dict[SeriesKey, int] = {
        series.descriptor.key: index
        for index, series in enumerate(visible_series)
    }
    secondary_axis = None
    rendered_curve_count = 0

    if extrema_config is not None and extrema_config.enabled and extrema_config.source_series_keys:
        detected_extrema = detect_extrema_for_sessions(visible_sessions, extrema_config)
        grouped_extrema = group_aligned_extrema(
            detected_extrema,
            alignment_tolerance_seconds=extrema_config.alignment_tolerance_seconds,
            reference_session_id=get_reference_session_id(visible_sessions),
        )
        fallback_order_by_series = build_fallback_series_order(visible_series, grouped_extrema)
        render_extrema_markers(
            axis,
            grouped_extrema,
            visible_range_seconds=(visible_start_seconds, visible_end_seconds),
            line_color_by_series=line_color_by_series,
            color_by_series=color_by_series,
            point_colors=extrema_point_colors,
            fallback_order_by_series=fallback_order_by_series,
        )

        target_axis = axis
        if extrema_config.use_secondary_axis:
            target_axis = axis.twinx()
            secondary_axis = target_axis

        rendered_curve_count = render_assigned_curves(
            target_axis,
            visible_sessions,
            grouped_extrema,
            assignments=assignment_map,
            visible_range_seconds=(visible_start_seconds, visible_end_seconds),
            line_color_by_series=line_color_by_series,
            color_by_series=color_by_series,
            point_colors=extrema_point_colors,
            fallback_order_by_series=fallback_order_by_series,
            line_width=chart_style.line_width,
        )
        if secondary_axis is not None and rendered_curve_count > 0:
            configure_secondary_value_axis(secondary_axis, chart_style)
        elif secondary_axis is not None and rendered_curve_count == 0:
            figure.delaxes(secondary_axis)
            secondary_axis = None

    if chart_style.curve_only_mode:
        axis.grid(False)
    elif chart_style.show_grid:
        grid_kwargs: dict[str, object] = {
            "linestyle": "--",
            "linewidth": 0.8,
            "alpha": chart_style.grid_alpha,
        }
        if chart_style.grid_color:
            grid_kwargs["color"] = chart_style.grid_color
        axis.grid(True, **grid_kwargs)
    else:
        axis.grid(False)

    font_family = normalize_font_family(chart_style.font_family)

    if chart_style.title and not chart_style.curve_only_mode:
        title_kwargs: dict[str, object] = {}
        if font_family:
            title_kwargs["fontfamily"] = font_family
        axis.set_title(chart_style.title, **title_kwargs)

    if chart_style.show_legend and not chart_style.curve_only_mode:
        legend_handles, legend_labels = axis.get_legend_handles_labels()
        if secondary_axis is not None:
            extra_handles, extra_labels = secondary_axis.get_legend_handles_labels()
            legend_handles.extend(extra_handles)
            legend_labels.extend(extra_labels)

    else:
        legend_handles = []
        legend_labels = []

    if chart_style.show_legend and len(legend_handles) > 1 and not chart_style.curve_only_mode:
        legend_kwargs: dict[str, object] = {
            "frameon": False,
            "loc": chart_style.legend_location,
        }
        if font_family:
            legend_kwargs["prop"] = {
                "family": font_family,
                "size": 9,
            }
        else:
            legend_kwargs["fontsize"] = 9
        legend = axis.legend(legend_handles, legend_labels, **legend_kwargs)
        if chart_style.legend_text_color:
            for legend_text in legend.get_texts():
                legend_text.set_color(chart_style.legend_text_color)

    configure_axis_visibility(axis, chart_style)

    logger.info(
        (
            "Built comparison figure plotted_lines=%d visible_series=%d grouped_extrema=%d "
            "assigned_curve_groups=%d elapsed_ms=%.2f"
        ),
        plotted_line_count,
        len(visible_series),
        len(grouped_extrema),
        rendered_curve_count,
        (perf_counter() - started_at) * 1000,
    )
    return figure


def build_comparison_output_name(
    sessions: Sequence[LoadedCsvSession],
    selected_series: Sequence[SeriesKey],
) -> str:
    session_by_id = {session.session_id: session for session in sessions}

    selected_session_ids: list[str] = []
    for series_key in selected_series:
        if series_key.session_id not in session_by_id:
            continue
        if series_key.session_id not in selected_session_ids:
            selected_session_ids.append(series_key.session_id)

    selected_aliases = [
        sanitize_filename((session_by_id[session_id].alias or session_by_id[session_id].data.source_path.stem).replace(" ", "_"))
        for session_id in selected_session_ids
    ]
    if not selected_aliases:
        selected_aliases = ["files"]

    if len(selected_aliases) > 2:
        file_part = f"{len(selected_aliases)}_files"
    else:
        file_part = "__".join(selected_aliases)

    selected_column_names: list[str] = []
    for series_key in selected_series:
        session = session_by_id.get(series_key.session_id)
        if session is None:
            continue
        column_name = sanitize_filename(session.data.column_for_index(series_key.column_index).name.replace(" ", "_"))
        if column_name not in selected_column_names:
            selected_column_names.append(column_name)

    if len(selected_column_names) > 2:
        metric_parts = selected_column_names[:2] + [f"and_{len(selected_column_names) - 2}_more"]
    else:
        metric_parts = selected_column_names
    metric_part = "_".join(metric_parts) if metric_parts else "chart"

    return f"compare__{file_part}__{metric_part}.png"


def normalize_extrema_mode(mode: str) -> str:
    normalized_mode = mode.strip().lower()
    if normalized_mode not in EXTREMA_MODES:
        raise ValueError(f"不支持的峰谷检测模式：{mode}")
    return normalized_mode


def smooth_series_values(values: Sequence[float], smoothing_window: int) -> list[float]:
    resolved_window = int(smoothing_window)
    if resolved_window <= 1 or len(values) <= 2:
        return [float(value) for value in values]

    radius_before = (resolved_window - 1) // 2
    radius_after = resolved_window // 2
    smoothed_values: list[float] = []
    for index in range(len(values)):
        start_index = max(0, index - radius_before)
        end_index = min(len(values), index + radius_after + 1)
        window_values = [float(value) for value in values[start_index:end_index]]
        smoothed_values.append(sum(window_values) / len(window_values))

    return smoothed_values


def resolve_distance_samples(
    source_seconds: Sequence[float],
    min_distance_seconds: float,
) -> int | None:
    if min_distance_seconds <= 0 or len(source_seconds) < 2:
        return None

    positive_deltas = [
        float(current_seconds) - float(previous_seconds)
        for previous_seconds, current_seconds in zip(source_seconds, source_seconds[1:])
        if float(current_seconds) > float(previous_seconds)
    ]
    if not positive_deltas:
        return None

    typical_delta = median(positive_deltas)
    if typical_delta <= 0:
        return None
    return max(1, int(round(float(min_distance_seconds) / float(typical_delta))))


def build_detected_extrema(
    key: SeriesKey,
    *,
    kind: str,
    source_seconds: Sequence[float],
    source_values: Sequence[float],
    sample_indices: Sequence[int],
    offset_seconds: float,
    peak_indices: Sequence[int],
    peak_properties: Mapping[str, Sequence[float]],
) -> list[DetectedExtremum]:
    prominences = peak_properties.get("prominences", ())
    detected_extrema: list[DetectedExtremum] = []
    for position, peak_index in enumerate(peak_indices):
        source_index = int(peak_index)
        source_second = float(source_seconds[source_index])
        sample_index = int(sample_indices[source_index])
        prominence = float(prominences[position]) if position < len(prominences) else 0.0
        detected_extrema.append(
            DetectedExtremum(
                event_id=f"{key.session_id}:{key.column_index}:{kind}:{sample_index}",
                key=key,
                kind=kind,
                source_seconds=source_second,
                aligned_seconds=source_second + float(offset_seconds),
                source_value=float(source_values[source_index]),
                prominence=prominence,
                sample_index=sample_index,
            )
        )

    return detected_extrema


def trim_series_with_sample_indices(
    x_values: Sequence[float],
    y_values: Sequence[float],
    start_seconds: float,
    end_seconds: float,
) -> tuple[list[int], list[float], list[float]]:
    filtered_points = [
        (index, float(x_value), float(y_value))
        for index, (x_value, y_value) in enumerate(zip(x_values, y_values))
        if start_seconds <= float(x_value) <= end_seconds
    ]
    if not filtered_points:
        return [], [], []

    filtered_indices, filtered_x_values, filtered_y_values = zip(*filtered_points)
    return list(filtered_indices), list(filtered_x_values), list(filtered_y_values)


def resolve_extrema_group_anchor(
    members: Sequence[DetectedExtremum],
    *,
    reference_session_id: str | None = None,
) -> float:
    if not members:
        raise ValueError("分组成员不能为空。")

    if reference_session_id:
        reference_aligned_seconds = [
            float(member.aligned_seconds)
            for member in members
            if member.key.session_id == reference_session_id
        ]
        if reference_aligned_seconds:
            return float(median(reference_aligned_seconds))

    return float(median(float(member.aligned_seconds) for member in members))


def finalize_extrema_group(
    kind: str,
    members: Sequence[DetectedExtremum],
    *,
    reference_session_id: str | None = None,
) -> AlignedExtremaGroup:
    resolved_members = tuple(members)
    for _ in range(2):
        anchor_seconds = resolve_extrema_group_anchor(
            resolved_members,
            reference_session_id=reference_session_id,
        )
        resolved_members = dedupe_group_members_by_series_key(resolved_members, anchor_seconds)

    anchor_seconds = resolve_extrema_group_anchor(
        resolved_members,
        reference_session_id=reference_session_id,
    )
    return AlignedExtremaGroup(
        group_id="",
        kind=kind,
        anchor_seconds=anchor_seconds,
        members=tuple(
            sorted(
                resolved_members,
                key=lambda member: (
                    member.key.session_id,
                    member.key.column_index,
                    member.sample_index,
                ),
            )
        ),
    )


def dedupe_group_members_by_series_key(
    members: Sequence[DetectedExtremum],
    anchor_seconds: float,
) -> tuple[DetectedExtremum, ...]:
    selected_members: dict[SeriesKey, DetectedExtremum] = {}
    for member in members:
        current_member = selected_members.get(member.key)
        if current_member is None or is_better_group_member(
            member,
            current_member,
            anchor_seconds=anchor_seconds,
        ):
            selected_members[member.key] = member

    return tuple(selected_members.values())


def is_better_group_member(
    candidate: DetectedExtremum,
    current_member: DetectedExtremum,
    *,
    anchor_seconds: float,
) -> bool:
    candidate_distance = abs(float(candidate.aligned_seconds) - float(anchor_seconds))
    current_distance = abs(float(current_member.aligned_seconds) - float(anchor_seconds))
    if candidate_distance != current_distance:
        return candidate_distance < current_distance
    if float(candidate.prominence) != float(current_member.prominence):
        return float(candidate.prominence) > float(current_member.prominence)
    if float(candidate.aligned_seconds) != float(current_member.aligned_seconds):
        return float(candidate.aligned_seconds) < float(current_member.aligned_seconds)
    return int(candidate.sample_index) < int(current_member.sample_index)


def normalize_extrema_assignments(
    assignments: Mapping[ExtremaPointKey, float | None] | Sequence[ExtremaAssignment],
) -> dict[ExtremaPointKey, float | None]:
    if hasattr(assignments, "items"):
        return {
            point_key: None if assigned_value is None else float(assigned_value)
            for point_key, assigned_value in assignments.items()
        }

    return {
        assignment.point_key: (
            None if assignment.assigned_value is None else float(assignment.assigned_value)
        )
        for assignment in assignments
    }


def get_reference_session_id(sessions: Sequence[LoadedCsvSession]) -> str | None:
    reference_session = next((session for session in sessions if session.is_reference), None)
    return None if reference_session is None else reference_session.session_id


def build_fallback_series_order(
    visible_series: Sequence[VisibleSeries],
    groups: Sequence[AlignedExtremaGroup],
) -> dict[SeriesKey, int]:
    ordered_series_keys = [
        series.descriptor.key
        for series in visible_series
    ]
    ordered_series_keys.extend(
        member.key
        for group in groups
        for member in group.members
    )

    fallback_order: dict[SeriesKey, int] = {}
    for series_key in ordered_series_keys:
        if series_key not in fallback_order:
            fallback_order[series_key] = len(fallback_order)
    return fallback_order


def resolve_series_render_color(
    series_key: SeriesKey,
    *,
    line_color_by_series: Mapping[SeriesKey, str],
    color_by_series: Mapping[SeriesKey, str] | None,
    fallback_order_by_series: Mapping[SeriesKey, int],
) -> str:
    selected_color = line_color_by_series.get(series_key)
    if selected_color:
        return selected_color
    if color_by_series is not None:
        selected_color = color_by_series.get(series_key)
        if selected_color:
            return selected_color

    default_palette = rcParams["axes.prop_cycle"].by_key().get("color", ["#1f77b4"])
    palette_index = fallback_order_by_series.get(series_key, 0)
    return str(default_palette[palette_index % len(default_palette)])


def resolve_extrema_point_color(
    point_key: ExtremaPointKey,
    *,
    line_color_by_series: Mapping[SeriesKey, str],
    color_by_series: Mapping[SeriesKey, str] | None,
    point_colors: Mapping[ExtremaPointKey, str] | None,
    fallback_order_by_series: Mapping[SeriesKey, int],
) -> str:
    if point_colors is not None:
        selected_color = point_colors.get(point_key)
        if selected_color:
            return selected_color

    return resolve_series_render_color(
        point_key.key,
        line_color_by_series=line_color_by_series,
        color_by_series=color_by_series,
        fallback_order_by_series=fallback_order_by_series,
    )


def render_extrema_markers(
    axis,
    groups: Sequence[AlignedExtremaGroup],
    *,
    visible_range_seconds: tuple[float, float],
    line_color_by_series: Mapping[SeriesKey, str],
    color_by_series: Mapping[SeriesKey, str] | None,
    point_colors: Mapping[ExtremaPointKey, str] | None,
    fallback_order_by_series: Mapping[SeriesKey, int],
) -> int:
    visible_start_seconds, visible_end_seconds = visible_range_seconds
    marker_count = 0
    for group in groups:
        for member in group.members:
            if not visible_start_seconds <= float(member.aligned_seconds) <= visible_end_seconds:
                continue

            point_key = ExtremaPointKey(group.group_id, member.key)
            axis.scatter(
                [float(member.aligned_seconds)],
                [float(member.source_value)],
                color=resolve_extrema_point_color(
                    point_key,
                    line_color_by_series=line_color_by_series,
                    color_by_series=color_by_series,
                    point_colors=point_colors,
                    fallback_order_by_series=fallback_order_by_series,
                ),
                s=30,
                marker="o",
                zorder=4,
                label="_nolegend_",
            )
            marker_count += 1

    return marker_count


def render_assigned_curves(
    axis,
    sessions: Sequence[LoadedCsvSession],
    groups: Sequence[AlignedExtremaGroup],
    *,
    assignments: Mapping[ExtremaPointKey, float | None],
    visible_range_seconds: tuple[float, float],
    line_color_by_series: Mapping[SeriesKey, str],
    color_by_series: Mapping[SeriesKey, str] | None,
    point_colors: Mapping[ExtremaPointKey, str] | None,
    fallback_order_by_series: Mapping[SeriesKey, int],
    line_width: float,
) -> int:
    visible_start_seconds, visible_end_seconds = visible_range_seconds
    session_by_id = {session.session_id: session for session in sessions}
    assigned_curve_points = build_assigned_curve_points(groups, assignments)
    rendered_curve_count = 0

    for series_key, (x_values, y_values) in assigned_curve_points.items():
        trimmed_x_values, trimmed_y_values = trim_series_to_range(
            x_values,
            y_values,
            visible_start_seconds,
            visible_end_seconds,
        )
        if not trimmed_y_values or not any(not math.isnan(value) for value in trimmed_y_values):
            continue

        session = session_by_id.get(series_key.session_id)
        session_alias = (
            session.alias.strip() or session.data.source_path.stem
            if session is not None
            else series_key.session_id
        )
        axis.plot(
            trimmed_x_values,
            trimmed_y_values,
            linewidth=max(float(line_width), 1.2),
            linestyle="--",
            color=resolve_series_render_color(
                series_key,
                line_color_by_series=line_color_by_series,
                color_by_series=color_by_series,
                fallback_order_by_series=fallback_order_by_series,
            ),
            label=f"{session_alias} · 赋值曲线",
        )
        rendered_curve_count += 1

    for group in groups:
        if not visible_start_seconds <= float(group.anchor_seconds) <= visible_end_seconds:
            continue
        for member in group.members:
            point_key = ExtremaPointKey(group.group_id, member.key)
            assigned_value = assignments.get(point_key)
            if assigned_value is None:
                continue

            axis.scatter(
                [float(group.anchor_seconds)],
                [float(assigned_value)],
                color=resolve_extrema_point_color(
                    point_key,
                    line_color_by_series=line_color_by_series,
                    color_by_series=color_by_series,
                    point_colors=point_colors,
                    fallback_order_by_series=fallback_order_by_series,
                ),
                s=30,
                marker="o",
                zorder=4,
                label="_nolegend_",
            )

    return rendered_curve_count


def configure_secondary_value_axis(axis, chart_style: ChartStyle) -> None:
    axis.set_facecolor("none")
    axis.yaxis.get_offset_text().set_visible(False)
    axis.set_ylabel("赋值曲线")

    if chart_style.curve_only_mode:
        for spine in axis.spines.values():
            spine.set_visible(False)
        axis.tick_params(
            axis="both",
            which="both",
            left=False,
            right=False,
            labelleft=False,
            labelright=False,
            bottom=False,
            top=False,
            labelbottom=False,
        )
        axis.yaxis.label.set_visible(False)
        return

    if chart_style.axis_color:
        axis.spines["right"].set_edgecolor(chart_style.axis_color)

    tick_kwargs: dict[str, object] = {
        "axis": "y",
        "which": "both",
        "labelright": chart_style.show_value_axis,
    }
    if chart_style.axis_color:
        tick_kwargs["color"] = chart_style.axis_color
    if chart_style.value_text_color:
        tick_kwargs["labelcolor"] = chart_style.value_text_color
        axis.yaxis.label.set_color(chart_style.value_text_color)
    elif chart_style.axis_color:
        axis.yaxis.label.set_color(chart_style.axis_color)

    axis.tick_params(**tick_kwargs)
    if not chart_style.show_value_axis:
        axis.yaxis.label.set_visible(False)



def build_figure(
    data: CsvLogData,
    column_indices: Sequence[int],
    title: str | None = None,
    width_px: int = 1920,
    height_px: int = 1080,
    dpi: int = 160,
    style: ChartStyle | None = None,
    color_by_column: Mapping[int, str] | None = None,
    visible_range_seconds: tuple[float, float] | None = None,
) -> Figure:
    if not column_indices:
        raise ValueError("至少需要选择一个参数。")
    if width_px < 200 or height_px < 200:
        raise ValueError("输出尺寸过小，请至少使用 200 x 200。")
    if dpi < 24:
        raise ValueError("DPI 不能小于 24。")

    chart_style = resolve_chart_style(style, title=title)
    if chart_style.line_width <= 0:
        raise ValueError("曲线线宽必须大于 0。")
    if not 0 <= chart_style.grid_alpha <= 1:
        raise ValueError("网格透明度必须在 0 到 1 之间。")
    if chart_style.legend_location not in LEGEND_LOCATIONS:
        raise ValueError(f"不支持的图例位置：{chart_style.legend_location}")
    if not MIN_TIME_TICK_DENSITY <= chart_style.time_tick_density <= MAX_TIME_TICK_DENSITY:
        raise ValueError(
            f"时间刻度密度必须在 {MIN_TIME_TICK_DENSITY} 到 {MAX_TIME_TICK_DENSITY} 之间。"
        )
    if chart_style.fixed_time_interval_seconds is not None and chart_style.fixed_time_interval_seconds <= 0:
        raise ValueError("固定时间刻度间隔必须大于 0。")
    validate_chart_style_colors(chart_style)
    visible_start_seconds, visible_end_seconds = resolve_visible_range_seconds(data, visible_range_seconds)

    configure_matplotlib_fonts()

    figure = Figure(
        figsize=(width_px / dpi, height_px / dpi),
        dpi=dpi,
        constrained_layout=True,
    )
    axis = figure.add_subplot(111)
    figure.patch.set_alpha(0.0)
    axis.set_facecolor("none")

    configure_time_axis(axis, visible_start_seconds, visible_end_seconds, chart_style)

    plotted_line_count = 0
    for column_index in column_indices:
        sensor_column = data.column_for_index(column_index)
        x_values, y_values = data.extract_series(column_index)
        if not y_values:
            continue
        x_values, y_values = trim_series_to_range(
            x_values,
            y_values,
            visible_start_seconds,
            visible_end_seconds,
        )
        if not y_values:
            continue
        line_kwargs: dict[str, object] = {}
        if color_by_column is not None:
            selected_color = color_by_column.get(column_index)
            if selected_color:
                line_kwargs["color"] = selected_color

        axis.plot(
            x_values,
            y_values,
            linewidth=chart_style.line_width,
            label=sensor_column.display_name,
            **line_kwargs,
        )
        plotted_line_count += 1

    if plotted_line_count == 0:
        raise ValueError("所选参数没有可用于绘图的数值数据。")

    if chart_style.curve_only_mode:
        axis.grid(False)
    elif chart_style.show_grid:
        grid_kwargs: dict[str, object] = {
            "linestyle": "--",
            "linewidth": 0.8,
            "alpha": chart_style.grid_alpha,
        }
        if chart_style.grid_color:
            grid_kwargs["color"] = chart_style.grid_color
        axis.grid(True, **grid_kwargs)
    else:
        axis.grid(False)

    font_family = normalize_font_family(chart_style.font_family)

    if chart_style.title and not chart_style.curve_only_mode:
        title_kwargs: dict[str, object] = {}
        if font_family:
            title_kwargs["fontfamily"] = font_family
        axis.set_title(chart_style.title, **title_kwargs)

    if chart_style.show_legend and plotted_line_count > 1 and not chart_style.curve_only_mode:
        legend_kwargs: dict[str, object] = {
            "frameon": False,
            "loc": chart_style.legend_location,
        }
        if font_family:
            legend_kwargs["prop"] = {
                "family": font_family,
                "size": 9,
            }
        else:
            legend_kwargs["fontsize"] = 9
        legend = axis.legend(**legend_kwargs)
        if chart_style.legend_text_color:
            for legend_text in legend.get_texts():
                legend_text.set_color(chart_style.legend_text_color)

    configure_axis_visibility(axis, chart_style)

    return figure


def save_figure(figure: Figure, output_path: Path | str) -> Path:
    destination = Path(output_path).expanduser().resolve()
    logger.info("Saving figure path=%s", destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    png_bytes = render_figure_png_bytes(figure)
    destination.write_bytes(png_bytes)
    logger.info("Saved figure path=%s bytes=%d", destination, len(png_bytes))
    return destination


def render_figure_png_bytes(figure: Figure) -> bytes:
    buffer = io.BytesIO()
    figure.savefig(
        buffer,
        format="png",
        transparent=True,
        bbox_inches="tight",
        pad_inches=0.15,
    )
    return buffer.getvalue()


def build_default_output_name(data: CsvLogData, column_indices: Sequence[int]) -> str:
    parts = [sanitize_filename(data.column_for_index(index).name) for index in column_indices[:3]]
    if len(column_indices) > 3:
        parts.append(f"and_{len(column_indices) - 3}_more")

    suffix = "__".join(part for part in parts if part)
    if not suffix:
        suffix = "chart"
    return f"{sanitize_filename(data.source_path.stem)}__{suffix}.png"


def sanitize_filename(text: str) -> str:
    sanitized = re.sub(r'[<>:"/\\|?*]+', "_", text).strip(" .")
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized[:80] or "chart"


def resolve_chart_style(style: ChartStyle | None, title: str | None = None) -> ChartStyle:
    if style is None:
        return ChartStyle(title=title)
    if title is not None and not style.title:
        return replace(style, title=title)
    return style


def validate_chart_style_colors(chart_style: ChartStyle) -> None:
    color_fields = (
        ("坐标轴颜色", chart_style.axis_color),
        ("网格颜色", chart_style.grid_color),
        ("时间文字颜色", chart_style.time_text_color),
        ("数值文字颜色", chart_style.value_text_color),
        ("图例文字颜色", chart_style.legend_text_color),
    )
    for field_name, color_text in color_fields:
        if color_text is not None and not is_color_like(color_text):
            raise ValueError(f"{field_name}无效：{color_text}")


def normalize_font_family(font_family: str | None) -> str | None:
    if font_family is None:
        return None

    normalized_font_family = font_family.strip()
    return normalized_font_family or None


def list_available_font_families() -> tuple[str, ...]:
    available_fonts = {font.name.strip() for font in font_manager.fontManager.ttflist if font.name.strip()}
    ordered_preferred_fonts = [font_name for font_name in FONT_CANDIDATES if font_name in available_fonts]
    remaining_fonts = sorted(available_fonts - set(ordered_preferred_fonts), key=str.casefold)
    return tuple(ordered_preferred_fonts + remaining_fonts)


def resolve_visible_range_seconds(
    data: CsvLogData,
    visible_range_seconds: tuple[float, float] | None,
) -> tuple[float, float]:
    if not data.elapsed_seconds:
        return 0.0, 1.0

    data_start = data.elapsed_seconds[0]
    data_end = data.elapsed_seconds[-1]
    if visible_range_seconds is None:
        return data_start, data_end if data_end > data_start else data_start + 1.0

    start_seconds, end_seconds = visible_range_seconds
    start_seconds = max(data_start, float(start_seconds))
    end_seconds = min(data_end, float(end_seconds))
    if end_seconds <= start_seconds:
        end_seconds = start_seconds + 1.0
    return start_seconds, end_seconds


def trim_series_to_range(
    x_values: Sequence[float],
    y_values: Sequence[float],
    start_seconds: float,
    end_seconds: float,
) -> tuple[list[float], list[float]]:
    filtered_pairs = [
        (x_value, y_value)
        for x_value, y_value in zip(x_values, y_values)
        if start_seconds <= x_value <= end_seconds
    ]
    if not filtered_pairs:
        return [], []

    filtered_x_values, filtered_y_values = zip(*filtered_pairs)
    return list(filtered_x_values), list(filtered_y_values)


def configure_time_axis(axis, start_seconds: float, end_seconds: float, chart_style: ChartStyle) -> None:
    locator = build_time_locator(start_seconds, end_seconds, chart_style)
    formatter = FuncFormatter(format_compact_elapsed_time)
    axis.xaxis.set_major_locator(locator)
    axis.xaxis.set_major_formatter(formatter)
    axis.xaxis.get_offset_text().set_visible(False)
    axis.set_xlim(start_seconds, end_seconds)


def configure_axis_visibility(axis, chart_style: ChartStyle) -> None:
    if chart_style.curve_only_mode:
        configure_curve_only_mode(axis)
        return

    if chart_style.axis_color:
        for spine in axis.spines.values():
            spine.set_edgecolor(chart_style.axis_color)

    x_tick_kwargs: dict[str, object] = {
        "axis": "x",
        "which": "both",
        "labelbottom": chart_style.show_time_axis,
    }
    y_tick_kwargs: dict[str, object] = {
        "axis": "y",
        "which": "both",
        "labelleft": chart_style.show_value_axis,
    }
    if chart_style.axis_color:
        x_tick_kwargs["color"] = chart_style.axis_color
        y_tick_kwargs["color"] = chart_style.axis_color
    if chart_style.time_text_color:
        x_tick_kwargs["labelcolor"] = chart_style.time_text_color
        axis.xaxis.get_offset_text().set_color(chart_style.time_text_color)
    if chart_style.value_text_color:
        y_tick_kwargs["labelcolor"] = chart_style.value_text_color
        axis.yaxis.get_offset_text().set_color(chart_style.value_text_color)

    axis.tick_params(**x_tick_kwargs)
    axis.tick_params(**y_tick_kwargs)
    apply_axis_font_family(axis, chart_style)
    axis.xaxis.get_offset_text().set_visible(False)
    axis.yaxis.get_offset_text().set_visible(False)


def apply_axis_font_family(axis, chart_style: ChartStyle) -> None:
    font_family = normalize_font_family(chart_style.font_family)
    if not font_family:
        return

    axis.title.set_fontfamily(font_family)
    axis.xaxis.get_offset_text().set_fontfamily(font_family)
    axis.yaxis.get_offset_text().set_fontfamily(font_family)
    for tick_label in [*axis.get_xticklabels(), *axis.get_yticklabels()]:
        tick_label.set_fontfamily(font_family)


def configure_curve_only_mode(axis) -> None:
    axis.tick_params(
        axis="both",
        which="both",
        bottom=False,
        top=False,
        left=False,
        right=False,
        labelbottom=False,
        labelleft=False,
    )
    for spine in axis.spines.values():
        spine.set_visible(False)
    axis.xaxis.get_offset_text().set_visible(False)
    axis.yaxis.get_offset_text().set_visible(False)


def build_time_locator(start_seconds: float, end_seconds: float, chart_style: ChartStyle):
    if chart_style.fixed_time_interval_seconds is not None:
        return build_fixed_interval_locator(chart_style.fixed_time_interval_seconds)

    span_seconds = max(end_seconds - start_seconds, 1.0)
    interval_seconds = resolve_tick_interval_seconds(span_seconds, chart_style.time_tick_density)
    return MultipleLocator(interval_seconds)


def format_elapsed_time(value: float, _position=None, *, omit_zero_hours: bool = False) -> str:
    total_seconds = int(round(value))
    sign_prefix = "-" if total_seconds < 0 else ""
    total_seconds = abs(total_seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if omit_zero_hours and hours == 0:
        return f"{sign_prefix}{minutes:02d}:{seconds:02d}"
    return f"{sign_prefix}{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_compact_elapsed_time(value: float, position=None) -> str:
    return format_elapsed_time(value, position, omit_zero_hours=True)


def resolve_tick_interval_seconds(span_seconds: float, time_tick_density: int) -> int:
    target_tick_count = 4 + time_tick_density * 2
    ideal_interval_seconds = span_seconds / max(target_tick_count, 1)
    candidate_intervals = [
        1,
        2,
        5,
        10,
        15,
        20,
        30,
        60,
        120,
        180,
        240,
        300,
        600,
        900,
        1200,
        1800,
        3600,
        7200,
        10800,
        14400,
        21600,
        43200,
    ]
    for interval_seconds in candidate_intervals:
        if interval_seconds >= ideal_interval_seconds:
            return interval_seconds
    return candidate_intervals[-1]


def build_fixed_interval_locator(fixed_time_interval_seconds: int):
    return MultipleLocator(fixed_time_interval_seconds)


def configure_matplotlib_fonts() -> None:
    global _FONT_READY
    if _FONT_READY:
        return

    available_fonts = {font.name for font in font_manager.fontManager.ttflist}
    selected_fonts = [font_name for font_name in FONT_CANDIDATES if font_name in available_fonts]
    if not selected_fonts:
        selected_fonts = ["Segoe UI"]

    rcParams["font.family"] = ["sans-serif"]
    rcParams["font.sans-serif"] = selected_fonts + list(rcParams.get("font.sans-serif", []))
    rcParams["axes.unicode_minus"] = False
    _FONT_READY = True
