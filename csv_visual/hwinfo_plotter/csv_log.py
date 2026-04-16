from __future__ import annotations

import codecs
import csv
import io
import logging
import re
import threading
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Protocol, Sequence

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
BOM_ENCODINGS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)

logger = logging.getLogger("csv_visual.csv_log")


def build_shared_column_key(name: str, occurrence: int) -> str:
    return f"{name}\x1f{occurrence}"


@dataclass(frozen=True)
class SensorColumn:
    index: int
    name: str
    occurrence: int
    display_name: str
    source_name: str | None = None
    shared_key: str = ""

    def __post_init__(self) -> None:
        if not self.shared_key:
            object.__setattr__(self, "shared_key", build_shared_column_key(self.name, self.occurrence))


@dataclass
class CsvLogData:
    source_path: Path
    encoding: str
    headers: list[str]
    columns: list[SensorColumn]
    timestamps: list[datetime]
    rows: list[list[str]]
    elapsed_seconds: list[float] = field(default_factory=list)
    skipped_rows: int = 0
    log_format: str = "generic"
    _column_map: dict[int, SensorColumn] = field(default_factory=dict, init=False, repr=False)
    _column_indices: tuple[int, ...] = field(default_factory=tuple, init=False, repr=False)
    _column_shared_key_map: dict[str, SensorColumn] = field(default_factory=dict, init=False, repr=False)
    _series_cache: dict[int, tuple[list[float], list[float]]] = field(default_factory=dict, init=False, repr=False)
    _all_series_preloaded: bool = field(default=False, init=False, repr=False)
    _series_cache_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.elapsed_seconds and self.timestamps:
            self.elapsed_seconds = build_elapsed_seconds(self.timestamps)
        self._column_map = {column.index: column for column in self.columns}
        self._column_indices = tuple(column.index for column in self.columns)
        self._column_shared_key_map = {column.shared_key: column for column in self.columns}

    def column_for_index(self, column_index: int) -> SensorColumn:
        try:
            return self._column_map[column_index]
        except KeyError as exc:
            raise KeyError(f"未找到列索引 {column_index}") from exc

    def find_column_by_shared_key(self, shared_key: str) -> SensorColumn | None:
        return self._column_shared_key_map.get(shared_key)

    def column_for_shared_key(self, shared_key: str) -> SensorColumn:
        column = self.find_column_by_shared_key(shared_key)
        if column is None:
            raise KeyError(f"未找到共享参数键 {shared_key}")
        return column

    def extract_series(self, column_index: int) -> tuple[list[float], list[float]]:
        with self._series_cache_lock:
            cached_series = self._series_cache.get(column_index)
        if cached_series is not None:
            return cached_series

        started_at = perf_counter()
        logger.debug(
            "Extracting numeric series source=%s column_index=%s rows=%d",
            self.source_path,
            column_index,
            len(self.rows),
        )
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
            logger.debug(
                "Extracted numeric series source=%s column_index=%s points=%d elapsed_ms=%.2f",
                self.source_path,
                column_index,
                len(y_values),
                (perf_counter() - started_at) * 1000,
            )
            return series

    def preload_numeric_series(self) -> None:
        with self._series_cache_lock:
            if self._all_series_preloaded:
                logger.debug("Numeric series already preloaded source=%s", self.source_path)
                return

        started_at = perf_counter()
        logger.info(
            "Preloading numeric series source=%s columns=%d rows=%d",
            self.source_path,
            len(self._column_indices),
            len(self.rows),
        )
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
            logger.info(
                "Preloaded numeric series source=%s cached_columns=%d elapsed_ms=%.2f",
                self.source_path,
                len(self._series_cache),
                (perf_counter() - started_at) * 1000,
            )


HWiNFOData = CsvLogData


class CsvLogLoader(Protocol):
    format_name: str

    def load(self, path: Path | str, preload_numeric: bool = False) -> CsvLogData:
        ...


_CSV_LOG_LOADERS: dict[str, CsvLogLoader] = {}


def register_csv_log_loader(loader: CsvLogLoader) -> None:
    _CSV_LOG_LOADERS[loader.format_name] = loader


def get_csv_log_loader(format_name: str) -> CsvLogLoader:
    try:
        return _CSV_LOG_LOADERS[format_name]
    except KeyError as exc:
        raise KeyError(f"未注册的 CSV 日志格式：{format_name}") from exc


def list_csv_log_formats() -> tuple[str, ...]:
    return tuple(sorted(_CSV_LOG_LOADERS))


def load_csv_log(path: Path | str, format_name: str = "hwinfo", preload_numeric: bool = False) -> CsvLogData:
    loader = get_csv_log_loader(format_name)
    return loader.load(path, preload_numeric=preload_numeric)


@dataclass(frozen=True)
class HWiNFOCsvLogLoader:
    format_name: str = "hwinfo"

    def load(self, path: Path | str, preload_numeric: bool = False) -> CsvLogData:
        source_path = Path(path).expanduser().resolve()
        logger.info(
            "Loading CSV file path=%s format=%s preload_numeric=%s",
            source_path,
            self.format_name,
            preload_numeric,
        )
        if not source_path.exists():
            raise FileNotFoundError(f"找不到 CSV 文件：{source_path}")

        raw_bytes = source_path.read_bytes()
        logger.debug("Read CSV bytes path=%s bytes=%d", source_path, len(raw_bytes))
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
        timestamps: list[datetime] = []
        rows: list[list[str]] = []
        skipped_rows = 0
        source_row: list[str] | None = None

        for row in reader:
            if not any(cell.strip() for cell in row):
                continue

            normalized_row = normalize_row_length(row, len(headers))
            if is_repeated_header_row(normalized_row, headers):
                skipped_rows += 1
                continue

            if is_hwinfo_source_row(normalized_row, date_index, time_index):
                source_row = normalized_row
                skipped_rows += 1
                continue

            try:
                timestamp = parse_timestamp(normalized_row[date_index], normalized_row[time_index])
            except ValueError:
                skipped_rows += 1
                continue

            timestamps.append(timestamp)
            rows.append(normalized_row)

        if not timestamps:
            raise ValueError("CSV 中没有成功解析出任何时间戳数据。")

        columns = build_hwinfo_sensor_columns(
            headers,
            date_index,
            time_index,
            source_row=source_row,
        )
        data = CsvLogData(
            source_path=source_path,
            encoding=encoding_used,
            headers=headers,
            columns=columns,
            timestamps=timestamps,
            elapsed_seconds=build_elapsed_seconds(timestamps),
            rows=rows,
            skipped_rows=skipped_rows,
            log_format=self.format_name,
        )

        if preload_numeric:
            data.preload_numeric_series()

        duration_seconds = data.elapsed_seconds[-1] if data.elapsed_seconds else 0.0
        logger.info(
            (
                "Loaded CSV file path=%s format=%s encoding=%s headers=%d sensor_columns=%d "
                "rows=%d skipped_rows=%d duration_seconds=%.3f preload_numeric=%s"
            ),
            source_path,
            self.format_name,
            encoding_used,
            len(headers),
            len(columns),
            len(rows),
            skipped_rows,
            float(duration_seconds),
            preload_numeric,
        )
        return data


def load_hwinfo_csv(path: Path | str, preload_numeric: bool = False) -> CsvLogData:
    return load_csv_log(path, format_name="hwinfo", preload_numeric=preload_numeric)


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
    score += header_line.count("Ã¯Â»Â¿") * 20

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


def build_hwinfo_sensor_columns(
    headers: Sequence[str],
    date_index: int,
    time_index: int,
    *,
    source_row: Sequence[str] | None = None,
) -> list[SensorColumn]:
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
        source_name = None
        if source_row is not None and index < len(source_row):
            cleaned_source_name = source_row[index].strip()
            source_name = cleaned_source_name or None

        columns.append(
            SensorColumn(
                index=index,
                name=name,
                occurrence=occurrence,
                display_name=display_name,
                source_name=source_name,
            )
        )

    return columns


def build_sensor_columns(headers: Sequence[str], date_index: int, time_index: int) -> list[SensorColumn]:
    return build_hwinfo_sensor_columns(headers, date_index, time_index)


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


def is_repeated_header_row(row: Sequence[str], headers: Sequence[str]) -> bool:
    return len(row) >= len(headers) and list(row[: len(headers)]) == list(headers)


def is_hwinfo_source_row(row: Sequence[str], date_index: int, time_index: int) -> bool:
    if row[date_index].strip() or row[time_index].strip():
        return False
    return any(cell.strip() for index, cell in enumerate(row) if index not in (date_index, time_index))


register_csv_log_loader(HWiNFOCsvLogLoader())

