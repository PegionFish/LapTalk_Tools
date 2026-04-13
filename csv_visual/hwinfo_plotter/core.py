from __future__ import annotations

import codecs
import csv
import io
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Sequence

from matplotlib import dates as mdates
from matplotlib import font_manager, rcParams
from matplotlib.figure import Figure

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
    x_label: str = "时间戳"
    y_label: str = "数值"
    line_width: float = 1.8
    show_grid: bool = True
    grid_alpha: float = 0.28
    show_legend: bool = True
    legend_location: str = "best"


@dataclass
class HWiNFOData:
    source_path: Path
    encoding: str
    headers: list[str]
    columns: list[SensorColumn]
    timestamps: list[datetime]
    rows: list[list[str]]
    skipped_rows: int = 0

    def column_for_index(self, column_index: int) -> SensorColumn:
        for column in self.columns:
            if column.index == column_index:
                return column
        raise KeyError(f"未找到列索引 {column_index}")

    def extract_series(self, column_index: int) -> tuple[list[datetime], list[float]]:
        x_values: list[datetime] = []
        y_values: list[float] = []

        for timestamp, row in zip(self.timestamps, self.rows):
            if column_index >= len(row):
                continue

            numeric_value = parse_numeric_value(row[column_index])
            if numeric_value is None:
                continue

            x_values.append(timestamp)
            y_values.append(numeric_value)

        return x_values, y_values


def load_hwinfo_csv(path: Path | str) -> HWiNFOData:
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

    return HWiNFOData(
        source_path=source_path,
        encoding=encoding_used,
        headers=headers,
        columns=columns,
        timestamps=timestamps,
        rows=rows,
        skipped_rows=skipped_rows,
    )


def build_figure(
    data: HWiNFOData,
    column_indices: Sequence[int],
    title: str | None = None,
    width_px: int = 1920,
    height_px: int = 1080,
    dpi: int = 160,
    style: ChartStyle | None = None,
) -> Figure:
    if not column_indices:
        raise ValueError("至少需要选择一个参数。")
    if width_px < 200 or height_px < 200:
        raise ValueError("输出尺寸过小，请至少使用 200 x 200。")
    if dpi < 72:
        raise ValueError("DPI 不能小于 72。")

    chart_style = resolve_chart_style(style, title=title)
    if chart_style.line_width <= 0:
        raise ValueError("曲线线宽必须大于 0。")
    if not 0 <= chart_style.grid_alpha <= 1:
        raise ValueError("网格透明度必须在 0 到 1 之间。")
    if chart_style.legend_location not in LEGEND_LOCATIONS:
        raise ValueError(f"不支持的图例位置：{chart_style.legend_location}")

    configure_matplotlib_fonts()

    figure = Figure(
        figsize=(width_px / dpi, height_px / dpi),
        dpi=dpi,
        constrained_layout=True,
    )
    axis = figure.add_subplot(111)
    figure.patch.set_alpha(0.0)
    axis.set_facecolor("none")

    locator = mdates.AutoDateLocator()
    axis.xaxis.set_major_locator(locator)
    axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))

    plotted_line_count = 0
    for column_index in column_indices:
        sensor_column = data.column_for_index(column_index)
        x_values, y_values = data.extract_series(column_index)
        if not y_values:
            continue

        axis.plot(
            x_values,
            y_values,
            linewidth=chart_style.line_width,
            label=sensor_column.display_name,
        )
        plotted_line_count += 1

    if plotted_line_count == 0:
        raise ValueError("所选参数没有可用于绘图的数值数据。")

    axis.set_xlabel(chart_style.x_label.strip() or "时间戳")
    axis.set_ylabel(chart_style.y_label.strip() or "数值")
    if chart_style.show_grid:
        axis.grid(True, linestyle="--", linewidth=0.8, alpha=chart_style.grid_alpha)
    else:
        axis.grid(False)

    if chart_style.title:
        axis.set_title(chart_style.title)

    if chart_style.show_legend and plotted_line_count > 1:
        axis.legend(frameon=False, fontsize=9, loc=chart_style.legend_location)

    return figure


def save_figure(figure: Figure, output_path: Path | str) -> Path:
    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(
        destination,
        transparent=True,
        bbox_inches="tight",
        pad_inches=0.15,
    )
    return destination


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
