from __future__ import annotations

import codecs
import csv
import io
import re
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

from matplotlib import font_manager, rcParams
from matplotlib.colors import is_color_like
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, MultipleLocator

ENCODING_CANDIDATES = (
    "utf-8-sig",
    "utf-8",
    "utf-16",
    "utf-16-le",
    "utf-16-be",
    "gb18030",
    "gbk",
    "mbcs",
    "cp1252",
)
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
BOM_ENCODINGS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)
MIN_TIME_TICK_DENSITY = 1
MAX_TIME_TICK_DENSITY = 12
DEFAULT_TIME_TICK_DENSITY = 7

_FONT_READY = False


@dataclass(frozen=True)
class SensorColumn:
    index: int
    name: str
    occurrence: int
    display_name: str


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


@dataclass
class HWiNFOData:
    source_path: Path
    encoding: str
    headers: list[str]
    columns: list[SensorColumn]
    timestamps: list[datetime]
    rows: list[list[str]]
    elapsed_seconds: list[float] = field(default_factory=list)
    skipped_rows: int = 0
    _column_map: dict[int, SensorColumn] = field(default_factory=dict, init=False, repr=False)
    _column_indices: tuple[int, ...] = field(default_factory=tuple, init=False, repr=False)
    _series_cache: dict[int, tuple[list[float], list[float]]] = field(default_factory=dict, init=False, repr=False)
    _all_series_preloaded: bool = field(default=False, init=False, repr=False)
    _series_cache_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.elapsed_seconds and self.timestamps:
            self.elapsed_seconds = build_elapsed_seconds(self.timestamps)
        self._column_map = {column.index: column for column in self.columns}
        self._column_indices = tuple(column.index for column in self.columns)

    def column_for_index(self, column_index: int) -> SensorColumn:
        try:
            return self._column_map[column_index]
        except KeyError as exc:
            raise KeyError(f"未找到列索引 {column_index}") from exc

    def extract_series(self, column_index: int) -> tuple[list[float], list[float]]:
        with self._series_cache_lock:
            cached_series = self._series_cache.get(column_index)
        if cached_series is not None:
            return cached_series

        x_values: list[float] = []
        y_values: list[float] = []

        for elapsed_seconds, row in zip(self.elapsed_seconds, self.rows):
            if column_index >= len(row):
                continue

            numeric_value = parse_numeric_value(row[column_index])
            if numeric_value is None:
                continue

            x_values.append(elapsed_seconds)
            y_values.append(numeric_value)

        series = (x_values, y_values)
        with self._series_cache_lock:
            cached_series = self._series_cache.get(column_index)
            if cached_series is not None:
                return cached_series
            self._series_cache[column_index] = series
            return series

    def preload_numeric_series(self) -> None:
        with self._series_cache_lock:
            if self._all_series_preloaded:
                return

        x_series_map = {column_index: [] for column_index in self._column_indices}
        y_series_map = {column_index: [] for column_index in self._column_indices}

        for elapsed_seconds, row in zip(self.elapsed_seconds, self.rows):
            for column_index in self._column_indices:
                if column_index >= len(row):
                    continue

                numeric_value = parse_numeric_value(row[column_index])
                if numeric_value is None:
                    continue

                x_series_map[column_index].append(elapsed_seconds)
                y_series_map[column_index].append(numeric_value)

        with self._series_cache_lock:
            if self._all_series_preloaded:
                return
            self._series_cache = {
                column_index: (x_series_map[column_index], y_series_map[column_index])
                for column_index in self._column_indices
            }
            self._all_series_preloaded = True


@dataclass(frozen=True)
class LoadedCsvSession:
    session_id: str
    alias: str
    data: HWiNFOData
    offset_seconds: float = 0.0
    is_reference: bool = False
    is_visible: bool = True
    preload_ready: bool = False
    preload_error: str | None = None


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


def compute_global_time_bounds(sessions: Sequence[LoadedCsvSession]) -> tuple[float, float]:
    visible_sessions = [session for session in sessions if session.is_visible and session.data.elapsed_seconds]
    if not visible_sessions:
        return 0.0, 1.0

    start_seconds = min(session.data.elapsed_seconds[0] + float(session.offset_seconds) for session in visible_sessions)
    end_seconds = max(session.data.elapsed_seconds[-1] + float(session.offset_seconds) for session in visible_sessions)
    if end_seconds <= start_seconds:
        end_seconds = start_seconds + 1.0
    return float(start_seconds), float(end_seconds)


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

        aligned_x_values = align_series_x_values(x_values, session.offset_seconds)
        trimmed_x_values, trimmed_y_values = trim_series_to_range(
            aligned_x_values,
            y_values,
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

    return visible_series


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
) -> Figure:
    visible_sessions = tuple(session for session in sessions if session.is_visible)
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
    for series in visible_series:
        line_kwargs: dict[str, object] = {}
        if color_by_series is not None:
            selected_color = color_by_series.get(series.descriptor.key)
            if selected_color:
                line_kwargs["color"] = selected_color

        axis.plot(
            list(series.x_values),
            list(series.y_values),
            linewidth=chart_style.line_width,
            label=(
                series.descriptor.legend_label
                if include_session_alias
                else series.descriptor.column_display_name
            ),
            **line_kwargs,
        )
        plotted_line_count += 1

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


def load_hwinfo_csv(path: Path | str, preload_numeric: bool = False) -> HWiNFOData:
    source_path = Path(path).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"找不到 CSV 文件：{source_path}")

    raw_bytes = source_path.read_bytes()
    text, encoding_used = decode_csv_bytes(raw_bytes)
    reader = csv.reader(io.StringIO(text))

    try:
        headers = next(reader)
    except StopIteration as exc:
        raise ValueError("CSV 文件为空。") from exc

    if len(headers) < 3:
        raise ValueError("CSV 文件列数不足，无法识别时间戳和传感器数据。")

    date_index = find_header_index(headers, "date", fallback=0)
    time_index = find_header_index(headers, "time", fallback=1)

    columns = build_sensor_columns(headers, date_index, time_index)
    timestamps: list[datetime] = []
    rows: list[list[str]] = []
    skipped_rows = 0

    for row in reader:
        if not any(cell.strip() for cell in row):
            continue

        normalized_row = normalize_row_length(row, len(headers))
        try:
            timestamp = parse_timestamp(normalized_row[date_index], normalized_row[time_index])
        except ValueError:
            skipped_rows += 1
            continue

        timestamps.append(timestamp)
        rows.append(normalized_row)

    if not timestamps:
        raise ValueError("CSV 中没有成功解析出任何时间戳数据。")

    data = HWiNFOData(
        source_path=source_path,
        encoding=encoding_used,
        headers=headers,
        columns=columns,
        timestamps=timestamps,
        elapsed_seconds=build_elapsed_seconds(timestamps),
        rows=rows,
        skipped_rows=skipped_rows,
    )

    if preload_numeric:
        data.preload_numeric_series()

    return data


def build_figure(
    data: HWiNFOData,
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
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(render_figure_png_bytes(figure))
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


def build_default_output_name(data: HWiNFOData, column_indices: Sequence[int]) -> str:
    parts = [sanitize_filename(data.column_for_index(index).name) for index in column_indices[:3]]
    if len(column_indices) > 3:
        parts.append(f"and_{len(column_indices) - 3}_more")

    suffix = "__".join(part for part in parts if part)
    if not suffix:
        suffix = "chart"
    return f"{sanitize_filename(data.source_path.stem)}__{suffix}.png"


def decode_csv_bytes(raw_bytes: bytes) -> tuple[str, str]:
    bom_encoding = detect_bom_encoding(raw_bytes)
    if bom_encoding is not None:
        try:
            return raw_bytes.decode(bom_encoding), bom_encoding
        except UnicodeError:
            return raw_bytes.decode(bom_encoding, errors="replace"), f"{bom_encoding} (errors=replace)"

    decoded_candidates: list[tuple[str, str]] = []
    seen_encodings: set[str] = set()
    for encoding in ENCODING_CANDIDATES:
        if encoding in seen_encodings:
            continue
        seen_encodings.add(encoding)

        try:
            decoded_candidates.append((encoding, raw_bytes.decode(encoding)))
        except (LookupError, UnicodeError):
            continue

    if decoded_candidates:
        text, encoding = choose_best_decoding(decoded_candidates)
        return text, encoding

    return raw_bytes.decode("utf-8-sig", errors="replace"), "utf-8-sig (errors=replace)"


def detect_bom_encoding(raw_bytes: bytes) -> str | None:
    for bom_bytes, encoding in BOM_ENCODINGS:
        if raw_bytes.startswith(bom_bytes):
            return encoding
    return None


def choose_best_decoding(decoded_candidates: Sequence[tuple[str, str]]) -> tuple[str, str]:
    ranked_candidates = [
        (score_decoded_text(text), index, encoding, text)
        for index, (encoding, text) in enumerate(decoded_candidates)
    ]
    _, _, encoding, text = min(ranked_candidates)
    return text, encoding


def score_decoded_text(text: str) -> int:
    sample = text[:8192]
    header_line = sample.splitlines()[0] if sample else ""

    score = 0
    score += sample.count("\ufffd") * 50
    score += sum(1 for char in sample if char == "\x00") * 80
    score += sum(1 for char in sample if ord(char) < 32 and char not in "\r\n\t") * 8
    score += sum(1 for char in sample if 0x7F <= ord(char) <= 0x9F) * 6
    score += header_line.count("ï»¿") * 20

    if "Date" in header_line:
        score -= 2
    if "Time" in header_line:
        score -= 2

    score -= min(count_cjk_characters(header_line), 12)
    return max(score, 0)


def count_cjk_characters(text: str) -> int:
    return sum(1 for char in text if is_cjk_character(char))


def is_cjk_character(char: str) -> bool:
    code_point = ord(char)
    return (
        0x3400 <= code_point <= 0x4DBF
        or 0x4E00 <= code_point <= 0x9FFF
        or 0xF900 <= code_point <= 0xFAFF
    )


def build_sensor_columns(headers: Sequence[str], date_index: int, time_index: int) -> list[SensorColumn]:
    counter = Counter(headers)
    occurrence_counter: defaultdict[str, int] = defaultdict(int)
    columns: list[SensorColumn] = []

    for index, name in enumerate(headers):
        if index in (date_index, time_index):
            continue

        occurrence_counter[name] += 1
        occurrence = occurrence_counter[name]
        duplicate_suffix = f" (#{occurrence})" if counter[name] > 1 else ""
        display_name = f"[{index:03}] {name}{duplicate_suffix}"

        columns.append(
            SensorColumn(
                index=index,
                name=name,
                occurrence=occurrence,
                display_name=display_name,
            )
        )

    return columns


def normalize_row_length(row: Sequence[str], expected_length: int) -> list[str]:
    normalized = list(row[:expected_length])
    if len(normalized) < expected_length:
        normalized.extend([""] * (expected_length - len(normalized)))
    return normalized


def find_header_index(headers: Sequence[str], target_name: str, fallback: int) -> int:
    for index, header in enumerate(headers):
        if header.strip().lower() == target_name:
            return index
    return fallback


def parse_timestamp(date_text: str, time_text: str) -> datetime:
    cleaned_date = date_text.strip()
    cleaned_time = time_text.strip().replace(",", ".")

    date_parts = [part for part in re.split(r"[./-]", cleaned_date) if part]
    if len(date_parts) != 3:
        raise ValueError(f"无法识别日期格式：{date_text}")

    day, month, year = (int(part) for part in date_parts)

    time_main, dot, fraction = cleaned_time.partition(".")
    time_parts = time_main.split(":")
    if len(time_parts) != 3:
        raise ValueError(f"无法识别时间格式：{time_text}")

    hour, minute, second = (int(part) for part in time_parts)
    microsecond = int((fraction + "000000")[:6]) if dot else 0

    return datetime(year, month, day, hour, minute, second, microsecond)


def build_elapsed_seconds(timestamps: Sequence[datetime]) -> list[float]:
    if not timestamps:
        return []

    base_timestamp = timestamps[0]
    return [(timestamp - base_timestamp).total_seconds() for timestamp in timestamps]


def parse_numeric_value(raw_value: str) -> float | None:
    cleaned = raw_value.strip().replace("\u00a0", "").replace("\u202f", "").replace(" ", "")
    if not cleaned:
        return None

    lowered = cleaned.lower()
    if lowered in {"yes", "no", "true", "false", "n/a"}:
        return None

    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")

    try:
        return float(cleaned)
    except ValueError:
        return None


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
    data: HWiNFOData,
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
