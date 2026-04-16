from __future__ import annotations

import base64
import ctypes
import logging
import sys
import threading
import tkinter as tk
import webbrowser
from collections.abc import Sequence
from ctypes import wintypes
from dataclasses import dataclass, field, replace
from pathlib import Path
from queue import Empty, Queue
from time import perf_counter
from tkinter import colorchooser, filedialog, messagebox, ttk
from uuid import uuid4

from .app_about import AboutInfo, get_app_about_info
from .csv_log import CsvLogData, SensorColumn, load_hwinfo_csv
from .core import (
    AlignedExtremaGroup,
    ChartStyle,
    DEFAULT_TIME_TICK_DENSITY,
    ExtremaDetectionConfig,
    ExtremaPointKey,
    LoadedCsvSession,
    MAX_TIME_TICK_DENSITY,
    MIN_TIME_TICK_DENSITY,
    SeriesDescriptor,
    SeriesKey,
    build_comparison_figure,
    build_comparison_output_name,
    build_series_descriptors,
    build_default_output_name,
    compute_session_active_timeline_range,
    compute_session_timeline_range,
    compute_global_time_bounds,
    detect_extrema_for_sessions,
    format_elapsed_time,
    format_compact_elapsed_time,
    group_aligned_extrema,
    list_available_font_families,
    normalize_offsets_for_reference,
    render_figure_png_bytes,
    resolve_session_source_trim_range,
    save_figure,
)
from .win32_image import get_png_dimensions, resize_png_bytes
from .runtime_logging import configure_runtime_logging, get_runtime_log_path


logger = logging.getLogger("csv_visual.gui")
LEGEND_LOCATION_CHOICES = {
    "自动": "best",
    "右上": "upper right",
    "左上": "upper left",
    "右下": "lower right",
    "左下": "lower left",
    "上方居中": "upper center",
    "下方居中": "lower center",
}
TIME_INTERVAL_UNIT_CHOICES = {
    "自动": None,
    "秒": 1,
    "分钟": 60,
    "小时": 3600,
}
EXTREMA_MODE_CHOICES = {
    "峰": "peak",
    "谷": "valley",
    "峰+谷": "both",
}
EXTREMA_KIND_LABELS = {
    "peak": "峰",
    "valley": "谷",
}
WINDOWS_DROP_MESSAGE = 0x0233
WINDOWS_NC_DESTROY_MESSAGE = 0x0082
WINDOWS_DRAG_QUERY_ALL_FILES = 0xFFFFFFFF
WINDOWS_LRESULT = getattr(wintypes, "LRESULT", ctypes.c_ssize_t)
WINDOWS_UINT_PTR = ctypes.c_size_t
WINDOWS_DWORD_PTR = ctypes.c_size_t


class WindowsFileDropManager:
    def __init__(self, root: tk.Misc) -> None:
        self.root = root
        self.dropped_file_paths: Queue[tuple[str, ...]] = Queue()
        self._shell32 = None
        self._comctl32 = None
        self._set_window_subclass = None
        self._remove_window_subclass = None
        self._def_subclass_proc = None
        self._window_proc_callback = None
        self._subclass_id = WINDOWS_UINT_PTR(id(self))
        self._registered_window_handles: set[int] = set()

    def register(self) -> bool:
        if sys.platform != "win32":
            return False

        try:
            self._load_win32_api()
            self.root.update_idletasks()
            for widget in self._iter_widgets(self.root):
                self._register_widget(widget)
        except (AttributeError, OSError, tk.TclError, ctypes.ArgumentError):
            self.unregister()
            return False

        return bool(self._registered_window_handles)

    def unregister(self) -> None:
        if (
            self._shell32 is None
            or self._remove_window_subclass is None
            or self._window_proc_callback is None
        ):
            return

        for window_handle in list(self._registered_window_handles):
            try:
                self._shell32.DragAcceptFiles(wintypes.HWND(window_handle), False)
                self._remove_window_subclass(
                    wintypes.HWND(window_handle),
                    self._window_proc_callback,
                    self._subclass_id,
                )
            except (OSError, ctypes.ArgumentError):
                pass

        self._registered_window_handles.clear()

    def pop_dropped_paths(self) -> tuple[str, ...] | None:
        try:
            return self.dropped_file_paths.get_nowait()
        except Empty:
            return None

    def _load_win32_api(self) -> None:
        if self._shell32 is not None:
            return

        self._shell32 = ctypes.windll.shell32
        self._comctl32 = ctypes.windll.comctl32
        self._set_window_subclass = self._comctl32.SetWindowSubclass
        self._set_window_subclass.argtypes = [
            wintypes.HWND,
            ctypes.c_void_p,
            WINDOWS_UINT_PTR,
            WINDOWS_DWORD_PTR,
        ]
        self._set_window_subclass.restype = wintypes.BOOL
        self._remove_window_subclass = self._comctl32.RemoveWindowSubclass
        self._remove_window_subclass.argtypes = [
            wintypes.HWND,
            ctypes.c_void_p,
            WINDOWS_UINT_PTR,
        ]
        self._remove_window_subclass.restype = wintypes.BOOL
        self._def_subclass_proc = self._comctl32.DefSubclassProc
        self._def_subclass_proc.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self._def_subclass_proc.restype = WINDOWS_LRESULT
        self._shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        self._shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
        self._shell32.DragQueryFileW.restype = wintypes.UINT
        self._shell32.DragFinish.argtypes = [wintypes.HANDLE]
        self._shell32.DragFinish.restype = None

        window_proc_factory = ctypes.WINFUNCTYPE(
            WINDOWS_LRESULT,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
            WINDOWS_UINT_PTR,
            WINDOWS_DWORD_PTR,
        )
        self._window_proc_callback = window_proc_factory(self._window_proc)

    def _register_widget(self, widget: tk.Misc) -> None:
        if self._shell32 is None or self._set_window_subclass is None or self._window_proc_callback is None:
            return

        window_handle = int(widget.winfo_id())
        if window_handle in self._registered_window_handles:
            return

        is_registered = self._set_window_subclass(
            wintypes.HWND(window_handle),
            self._window_proc_callback,
            self._subclass_id,
            0,
        )
        if not is_registered:
            return

        self._registered_window_handles.add(window_handle)
        self._shell32.DragAcceptFiles(wintypes.HWND(window_handle), True)

    def _window_proc(self, window_handle, message, w_param, l_param, _subclass_id, _ref_data):
        if message == WINDOWS_DROP_MESSAGE:
            dropped_paths = self._extract_dropped_paths(w_param)
            self.dropped_file_paths.put(dropped_paths)
            return 0

        if self._def_subclass_proc is None:
            return 0

        result = self._def_subclass_proc(window_handle, message, w_param, l_param)
        if message == WINDOWS_NC_DESTROY_MESSAGE:
            self._registered_window_handles.discard(int(window_handle))

        return result

    def _extract_dropped_paths(self, drop_handle) -> tuple[str, ...]:
        if self._shell32 is None:
            return ()

        try:
            file_count = self._shell32.DragQueryFileW(drop_handle, WINDOWS_DRAG_QUERY_ALL_FILES, None, 0)
            dropped_paths: list[str] = []
            for file_index in range(file_count):
                path_length = self._shell32.DragQueryFileW(drop_handle, file_index, None, 0)
                path_buffer = ctypes.create_unicode_buffer(path_length + 1)
                self._shell32.DragQueryFileW(drop_handle, file_index, path_buffer, path_length + 1)
                dropped_paths.append(path_buffer.value)
            return tuple(dropped_paths)
        finally:
            self._shell32.DragFinish(drop_handle)

    def _iter_widgets(self, widget: tk.Misc):
        yield widget
        for child_widget in widget.winfo_children():
            yield from self._iter_widgets(child_widget)


def fit_size_within_bounds(
    image_width: int,
    image_height: int,
    bounds_width: int,
    bounds_height: int,
) -> tuple[int, int]:
    if image_width <= 0 or image_height <= 0:
        raise ValueError("原图尺寸必须大于 0。")
    if bounds_width <= 0 or bounds_height <= 0:
        raise ValueError("预览区域尺寸必须大于 0。")

    scale = min(bounds_width / image_width, bounds_height / image_height)
    target_width = max(1, int(image_width * scale))
    target_height = max(1, int(image_height * scale))
    return target_width, target_height


@dataclass(frozen=True)
class PreviewRenderRequest:
    request_id: int
    sessions: tuple[LoadedCsvSession, ...]
    selected_series: tuple[SeriesKey, ...]
    width_px: int
    height_px: int
    dpi: int
    style: ChartStyle
    color_by_series: dict[SeriesKey, str]
    visible_range_seconds: tuple[float, float] | None = None
    extrema_config: ExtremaDetectionConfig | None = None
    extrema_assignments: dict[ExtremaPointKey, float] = field(default_factory=dict)
    extrema_point_colors: dict[ExtremaPointKey, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PreviewRenderResult:
    request_id: int
    png_bytes: bytes | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class PreloadSeriesRequest:
    request_id: int
    session_id: str
    data: CsvLogData


@dataclass(frozen=True)
class PreloadSeriesResult:
    request_id: int
    session_id: str
    error_message: str | None = None


class HWiNFOPlotterApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("HWiNFO CSV 折线图导出工具")
        self.geometry("1420x900")
        self.minsize(1180, 720)

        self.sessions: list[LoadedCsvSession] = []
        self.visible_parameter_columns: list[SensorColumn] = []
        self.visible_series_descriptors: list[SeriesDescriptor] = []
        self.selected_series_keys: set[SeriesKey] = set()
        self.series_colors: dict[SeriesKey, str] = {}
        self.detected_extrema_groups: tuple[AlignedExtremaGroup, ...] = ()
        self.extrema_assignments: dict[ExtremaPointKey, float] = {}
        self.extrema_point_colors: dict[ExtremaPointKey, str] = {}
        self.extrema_source_columns: list[SensorColumn] = []
        self.extrema_point_key_by_row_id: dict[str, ExtremaPointKey] = {}
        self.selected_extrema_point_key: ExtremaPointKey | None = None
        self.extrema_refresh_after_id: str | None = None
        self._updating_extrema_controls = False
        self._updating_session_editor = False
        self.preview_label: ttk.Label | None = None
        self.preview_image: tk.PhotoImage | None = None
        self.about_window: tk.Toplevel | None = None
        self.session_alias_entry: ttk.Entry | None = None
        self.session_offset_entry: ttk.Entry | None = None
        self.extrema_source_combobox: ttk.Combobox | None = None
        self.extrema_group_tree: ttk.Treeview | None = None
        self.preview_source_png_bytes: bytes | None = None
        self.preview_source_size: tuple[int, int] | None = None
        self.preview_display_size: tuple[int, int] | None = None
        self.preview_after_id: str | None = None
        self.preview_display_after_id: str | None = None
        self.file_drop_after_id: str | None = None
        self.process_results_after_id: str | None = None
        self.default_csv_after_id: str | None = None
        self.preview_request_id = 0
        self.active_preview_request_id = 0
        self.pending_preview_request: PreviewRenderRequest | None = None
        self.preview_results: Queue[PreviewRenderResult] = Queue()
        self.preview_request_lock = threading.Lock()
        self.preview_request_event = threading.Event()
        self.preview_shutdown_event = threading.Event()
        self.file_drop_manager: WindowsFileDropManager | None = None
        self.preview_worker = threading.Thread(target=self._preview_worker_loop, name="csv-visual-preview", daemon=True)
        self.preview_worker.start()
        self.preload_request_id = 0
        self.preload_results: Queue[PreloadSeriesResult] = Queue()
        self.preload_requests: Queue[PreloadSeriesRequest] = Queue()
        self.preload_worker = threading.Thread(target=self._preload_worker_loop, name="csv-visual-preload", daemon=True)
        self.preload_worker.start()
        self.font_family_choices = ("自动", *list_available_font_families())

        self.filter_var = tk.StringVar()
        self.session_alias_var = tk.StringVar()
        self.session_offset_var = tk.StringVar(value="0")
        self.session_source_trim_var = tk.StringVar(value="有效片段：--")
        self.extrema_enabled_var = tk.BooleanVar(value=False)
        self.extrema_source_var = tk.StringVar()
        self.extrema_mode_var = tk.StringVar(value="峰+谷")
        self.extrema_min_distance_var = tk.StringVar(value="1.0")
        self.extrema_min_prominence_var = tk.StringVar(value="0.0")
        self.extrema_smoothing_window_var = tk.StringVar(value="1")
        self.extrema_alignment_tolerance_var = tk.StringVar(value="1.0")
        self.extrema_selected_group_var = tk.StringVar(value="--")
        self.extrema_selected_file_var = tk.StringVar(value="--")
        self.extrema_selected_source_value_var = tk.StringVar(value="--")
        self.extrema_assignment_value_var = tk.StringVar()
        self.extrema_point_color_var = tk.StringVar()
        self.title_var = tk.StringVar()
        self.width_var = tk.StringVar(value="1920")
        self.height_var = tk.StringVar(value="1080")
        self.dpi_var = tk.StringVar(value="160")
        self.line_width_var = tk.StringVar(value="1.8")
        self.time_density_var = tk.DoubleVar(value=DEFAULT_TIME_TICK_DENSITY)
        self.time_density_label_var = tk.StringVar()
        self.fixed_time_interval_var = tk.StringVar()
        self.fixed_time_interval_unit_var = tk.StringVar(value="自动")
        self.timeline_zoom_factor = 1.0
        self.timeline_status_var = tk.StringVar(value="时间轴：默认自适应窗口；滚轮缩放，拖动片段对齐或裁剪。")
        self.timeline_pixels_per_second = 12.0
        self.timeline_start_seconds = 0.0
        self.timeline_end_seconds = 60.0
        self.timeline_drag_state: dict[str, object] | None = None
        self.timeline_clip_item_by_session_id: dict[str, int] = {}
        self.timeline_hit_regions: dict[int, tuple[str, str | None]] = {}
        self.timeline_preview_after_id: str | None = None
        self._refreshing_timeline = False
        self._updating_curve_only_mode = False
        self._suppress_chart_option_refresh = False
        self.curve_only_mode_var = tk.BooleanVar(value=False)
        self.show_grid_var = tk.BooleanVar(value=True)
        self.show_legend_var = tk.BooleanVar(value=True)
        self.show_time_axis_var = tk.BooleanVar(value=True)
        self.show_value_axis_var = tk.BooleanVar(value=True)
        self.legend_location_var = tk.StringVar(value="自动")
        self.selection_var = tk.StringVar(value="当前未选择参数")
        self.status_var = tk.StringVar(value="请添加一个或多个 HWiNFO CSV 文件。")
        self.series_color_var = tk.StringVar()
        self.axis_color_var = tk.StringVar()
        self.grid_color_var = tk.StringVar()
        self.time_text_color_var = tk.StringVar()
        self.value_text_color_var = tk.StringVar()
        self.legend_text_color_var = tk.StringVar()
        self.font_family_var = tk.StringVar(value="自动")

        self.filter_var.trace_add("write", self._on_filter_changed)
        for option_var in (
            self.title_var,
            self.width_var,
            self.height_var,
            self.dpi_var,
            self.line_width_var,
            self.fixed_time_interval_var,
            self.fixed_time_interval_unit_var,
            self.legend_location_var,
            self.axis_color_var,
            self.grid_color_var,
            self.time_text_color_var,
            self.value_text_color_var,
            self.legend_text_color_var,
            self.font_family_var,
        ):
            option_var.trace_add("write", self._on_chart_option_changed)
        for option_var in (
            self.show_grid_var,
            self.show_legend_var,
            self.show_time_axis_var,
            self.show_value_axis_var,
        ):
            option_var.trace_add("write", self._on_standard_chart_element_changed)
        self.curve_only_mode_var.trace_add("write", self._on_curve_only_mode_changed)
        self.time_density_var.trace_add("write", self._on_time_density_changed)
        self.extrema_enabled_var.trace_add("write", self._on_extrema_enabled_changed)
        for extrema_var in (
            self.extrema_source_var,
            self.extrema_mode_var,
            self.extrema_min_distance_var,
            self.extrema_min_prominence_var,
            self.extrema_smoothing_window_var,
            self.extrema_alignment_tolerance_var,
        ):
            extrema_var.trace_add("write", self._on_extrema_setting_changed)
        self.update_time_density_label()

        self._build_app_menu()
        self._build_layout()
        self.enable_file_drop()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.process_results_after_id = self.after(80, self.process_preview_results)

        default_csv = self._find_default_csv()
        if default_csv:
            self.default_csv_after_id = self.after(100, lambda path=default_csv: self.add_csv_files((path,)))
            logger.info("Default CSV auto-load scheduled path=%s", default_csv)

        logger.info(
            "Initialized HWiNFOPlotterApp log_path=%s preview_worker=%s preload_worker=%s",
            get_runtime_log_path(),
            self.preview_worker.name,
            self.preload_worker.name,
        )

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=7)
        self.rowconfigure(0, weight=1)
        self.rowconfigure(1, weight=0)

        self.left_column = ttk.Frame(self)
        self.left_column.grid(row=0, column=0, sticky="nsew", padx=(14, 8), pady=(14, 10))
        self.left_column.columnconfigure(0, weight=1)
        self.left_column.rowconfigure(0, weight=1)
        self.left_column.rowconfigure(1, weight=9)

        self.right_column = ttk.Frame(self)
        self.right_column.grid(row=0, column=1, sticky="nsew", padx=(0, 14), pady=(14, 10))
        self.right_column.columnconfigure(0, weight=1)
        self.right_column.rowconfigure(0, weight=17)
        self.right_column.rowconfigure(1, weight=3)

        self._build_file_management_module(self.left_column)
        self._build_parameter_and_chart_module(self.left_column)
        self._build_preview_module(self.right_column)
        self._build_time_editing_module(self.right_column)

        status_bar = ttk.Label(self, textvariable=self.status_var, relief=tk.SUNKEN, anchor="w", padding=(10, 6))
        status_bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        self._bind_mousewheel_events()
        self.refresh_timeline()

    def _build_app_menu(self) -> None:
        self.menu_bar = tk.Menu(self)
        self.help_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.help_menu.add_command(label="关于", command=self.open_about_window)
        self.menu_bar.add_cascade(label="帮助", menu=self.help_menu)
        self.configure(menu=self.menu_bar)

    def open_about_window(self) -> None:
        if self.about_window is not None and self.about_window.winfo_exists():
            self.about_window.deiconify()
            self.about_window.lift()
            self.about_window.focus_force()
            return

        about_info = get_app_about_info()
        about_window = tk.Toplevel(self)
        self.about_window = about_window
        about_window.title("关于")
        about_window.transient(self)
        about_window.resizable(False, False)
        about_window.protocol("WM_DELETE_WINDOW", self._on_about_window_closed)

        content = ttk.Frame(about_window, padding=(26, 22, 26, 20))
        content.grid(row=0, column=0, sticky="nsew")
        content.columnconfigure(0, weight=0)
        content.columnconfigure(1, weight=1)

        ttk.Label(
            content,
            text=about_info.app_name,
            font=("Segoe UI", 16, "bold"),
            anchor="center",
        ).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))

        version_frame = ttk.Frame(content, padding=(10, 8))
        version_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        version_frame.columnconfigure(1, weight=1)
        ttk.Label(version_frame, text=f"{about_info.version_label}:", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(version_frame, text=about_info.version, font=("Consolas", 9), wraplength=460).grid(
            row=0,
            column=1,
            sticky="w",
            padx=(8, 0),
        )

        ttk.Separator(content, orient=tk.HORIZONTAL).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 12))

        ttk.Label(content, text=about_info.distribution_prefix).grid(row=3, column=0, sticky="w", pady=3, padx=(0, 12))
        self._build_about_link_label(content, about_info.license_link.label, about_info.license_link.url).grid(
            row=3,
            column=1,
            sticky="w",
            pady=3,
        )

        ttk.Label(content, text="Repository").grid(row=4, column=0, sticky="w", pady=3, padx=(0, 12))
        self._build_about_link_label(content, about_info.repository_link.label, about_info.repository_link.url).grid(
            row=4,
            column=1,
            sticky="w",
            pady=3,
        )

        ttk.Label(content, text=about_info.author_prefix).grid(row=5, column=0, sticky="w", pady=3, padx=(0, 12))
        author_row = ttk.Frame(content)
        author_row.grid(row=5, column=1, sticky="w", pady=3)
        self._build_about_link_label(author_row, about_info.author_link.label, about_info.author_link.url).grid(row=0, column=0, sticky="w")
        ttk.Label(author_row, text=f" {about_info.affiliation_text} ").grid(row=0, column=1, sticky="w")
        self._build_about_link_label(author_row, about_info.organization_link.label, about_info.organization_link.url).grid(
            row=0,
            column=2,
            sticky="w",
        )

        ttk.Label(content, text="Email").grid(row=6, column=0, sticky="w", pady=3, padx=(0, 12))
        self._build_about_link_label(content, about_info.email_link.label, about_info.email_link.url).grid(
            row=6,
            column=1,
            sticky="w",
            pady=3,
        )

        ttk.Button(content, text="关闭", command=self._on_about_window_closed).grid(row=7, column=0, columnspan=2, sticky="e", pady=(18, 0))
        self._center_child_window(about_window)

    def _build_about_link_label(self, parent: tk.Misc, text: str, url: str) -> ttk.Label:
        link_label = ttk.Label(parent, text=text, foreground="#1f5fbf", cursor="hand2", font=("Segoe UI", 9, "underline"))
        link_label.bind("<Button-1>", lambda _event: self.open_external_link(url))
        return link_label

    def _center_child_window(self, child_window: tk.Toplevel) -> None:
        child_window.update_idletasks()
        child_width = child_window.winfo_reqwidth()
        child_height = child_window.winfo_reqheight()
        parent_x = self.winfo_rootx()
        parent_y = self.winfo_rooty()
        parent_width = max(self.winfo_width(), 1)
        parent_height = max(self.winfo_height(), 1)
        window_x = parent_x + max((parent_width - child_width) // 2, 0)
        window_y = parent_y + max((parent_height - child_height) // 2, 0)
        child_window.geometry(f"{child_width}x{child_height}+{window_x}+{window_y}")

    def _on_about_window_closed(self) -> None:
        if self.about_window is None:
            return

        about_window = self.about_window
        self.about_window = None
        if about_window.winfo_exists():
            about_window.destroy()

    def open_external_link(self, url: str) -> None:
        try:
            webbrowser.open(url)
        except webbrowser.Error as exc:
            messagebox.showerror("打开链接失败", str(exc))

    def _build_file_management_module(self, parent: tk.Misc) -> None:
        self.file_management_module = ttk.Labelframe(parent, text="文件管理", padding=(10, 8, 10, 8))
        self.file_management_module.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        self.file_management_module.columnconfigure(0, weight=1)
        self.file_management_module.rowconfigure(0, weight=1)

        session_table_frame = ttk.Frame(self.file_management_module)
        session_table_frame.grid(row=0, column=0, sticky="nsew")
        session_table_frame.columnconfigure(0, weight=1)
        session_table_frame.rowconfigure(0, weight=1)

        self.session_tree = ttk.Treeview(
            session_table_frame,
            columns=("filename", "alias", "duration"),
            show="headings",
            selectmode="extended",
            height=10,
        )
        self.session_tree.heading("filename", text="文件名")
        self.session_tree.heading("alias", text="别名")
        self.session_tree.heading("duration", text="时长")
        self.session_tree.column("filename", width=180, anchor="w")
        self.session_tree.column("alias", width=120, anchor="w")
        self.session_tree.column("duration", width=88, anchor="center")
        self.session_tree.grid(row=0, column=0, sticky="nsew")
        self.session_tree.bind("<<TreeviewSelect>>", self.on_session_selection_changed)

        session_scrollbar = ttk.Scrollbar(
            session_table_frame,
            orient=tk.VERTICAL,
            command=self.session_tree.yview,
        )
        session_scrollbar.grid(row=0, column=1, sticky="ns")
        self.session_tree.configure(yscrollcommand=session_scrollbar.set)

        file_button_row = ttk.Frame(self.file_management_module)
        file_button_row.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        file_button_row.columnconfigure((0, 1, 2, 3), weight=1)

        ttk.Button(file_button_row, text="添加 CSV...", command=self.browse_csv).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(file_button_row, text="移除选中", command=self.remove_selected_sessions).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(file_button_row, text="清空全部", command=self.clear_all_sessions).grid(row=0, column=2, sticky="ew", padx=4)
        ttk.Button(file_button_row, text="设为基准", command=self.set_selected_session_as_reference).grid(row=0, column=3, sticky="ew", padx=(4, 0))

        session_alias_row = ttk.Frame(self.file_management_module)
        session_alias_row.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        session_alias_row.columnconfigure(1, weight=1)

        ttk.Label(session_alias_row, text="选中文件别名").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.session_alias_entry = ttk.Entry(
            session_alias_row,
            textvariable=self.session_alias_var,
            state=tk.DISABLED,
        )
        self.session_alias_entry.grid(row=0, column=1, sticky="ew")
        self.session_alias_entry.bind("<Return>", self.apply_selected_session_alias)
        ttk.Button(session_alias_row, text="应用别名", command=self.apply_selected_session_alias).grid(
            row=0,
            column=2,
            sticky="ew",
            padx=(8, 0),
        )

    def _build_parameter_and_chart_module(self, parent: tk.Misc) -> None:
        self.parameter_chart_module = ttk.Labelframe(parent, text="参数与图表设置", padding=(10, 8, 10, 8))
        self.parameter_chart_module.grid(row=1, column=0, sticky="nsew")
        self.parameter_chart_module.columnconfigure(0, weight=1)
        self.parameter_chart_module.rowconfigure(0, weight=1)

        control_panel_wrapper = ttk.Frame(self.parameter_chart_module)
        control_panel_wrapper.grid(row=0, column=0, sticky="nsew")
        control_panel_wrapper.columnconfigure(0, weight=1)
        control_panel_wrapper.rowconfigure(0, weight=1)

        self.control_scroll_canvas = tk.Canvas(control_panel_wrapper, highlightthickness=0, borderwidth=0)
        self.control_scroll_canvas.grid(row=0, column=0, sticky="nsew")

        control_scrollbar = ttk.Scrollbar(
            control_panel_wrapper,
            orient=tk.VERTICAL,
            command=self.control_scroll_canvas.yview,
        )
        control_scrollbar.grid(row=0, column=1, sticky="ns")
        self.control_scroll_canvas.configure(yscrollcommand=control_scrollbar.set)

        control_panel = ttk.Frame(self.control_scroll_canvas, padding=(0, 0, 12, 0))
        self.control_panel = control_panel
        control_panel.columnconfigure(0, weight=1)
        self.control_window_id = self.control_scroll_canvas.create_window((0, 0), window=control_panel, anchor="nw")
        control_panel.bind("<Configure>", self._on_control_host_configure)
        self.control_scroll_canvas.bind("<Configure>", self._on_control_canvas_configure)

        ttk.Label(control_panel, text="参数筛选").grid(row=0, column=0, sticky="w")
        ttk.Entry(control_panel, textvariable=self.filter_var).grid(row=1, column=0, sticky="ew", pady=(4, 8))

        list_frame = ttk.Frame(control_panel)
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.column_listbox = tk.Listbox(
            list_frame,
            selectmode=tk.EXTENDED,
            exportselection=False,
            activestyle="none",
            height=14,
        )
        self.column_listbox.grid(row=0, column=0, sticky="nsew")
        self.column_listbox.bind("<<ListboxSelect>>", self.on_column_selection_changed)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.column_listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.column_listbox.configure(yscrollcommand=scrollbar.set)

        ttk.Label(control_panel, textvariable=self.selection_var).grid(row=3, column=0, sticky="w", pady=(8, 8))

        button_row = ttk.Frame(control_panel)
        button_row.grid(row=4, column=0, sticky="ew", pady=(0, 10))
        button_row.columnconfigure((0, 1), weight=1)

        ttk.Button(button_row, text="全选可见项", command=self.select_all_visible).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(button_row, text="清空选择", command=self.clear_selection).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        series_style_frame = ttk.Labelframe(control_panel, text="参数颜色", padding=12)
        series_style_frame.grid(row=5, column=0, sticky="nsew", pady=(0, 12))
        series_style_frame.columnconfigure(0, weight=1)
        series_style_frame.rowconfigure(0, weight=1)

        selected_series_frame = ttk.Frame(series_style_frame)
        selected_series_frame.grid(row=0, column=0, sticky="nsew")
        selected_series_frame.columnconfigure(0, weight=1)
        selected_series_frame.rowconfigure(0, weight=1)

        self.selected_series_listbox = tk.Listbox(
            selected_series_frame,
            selectmode=tk.EXTENDED,
            exportselection=False,
            activestyle="none",
            height=8,
        )
        self.selected_series_listbox.grid(row=0, column=0, sticky="nsew")

        selected_series_scrollbar = ttk.Scrollbar(
            selected_series_frame,
            orient=tk.VERTICAL,
            command=self.selected_series_listbox.yview,
        )
        selected_series_scrollbar.grid(row=0, column=1, sticky="ns")
        self.selected_series_listbox.configure(yscrollcommand=selected_series_scrollbar.set)

        color_input_row = ttk.Frame(series_style_frame)
        color_input_row.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        color_input_row.columnconfigure(1, weight=1)

        ttk.Label(color_input_row, text="HEX").grid(row=0, column=0, sticky="w", padx=(0, 8))
        series_color_entry = ttk.Entry(color_input_row, textvariable=self.series_color_var)
        series_color_entry.grid(row=0, column=1, sticky="ew")
        series_color_entry.bind("<Return>", self._on_series_color_submitted)

        color_button_row = ttk.Frame(series_style_frame)
        color_button_row.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        color_button_row.columnconfigure((0, 1, 2), weight=1)

        ttk.Button(color_button_row, text="应用颜色", command=self.apply_series_color).grid(
            row=0,
            column=0,
            sticky="ew",
            padx=(0, 4),
        )
        ttk.Button(color_button_row, text="取色器...", command=self.choose_series_color).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=4,
        )
        ttk.Button(color_button_row, text="清除颜色", command=self.clear_series_color).grid(
            row=0,
            column=2,
            sticky="ew",
            padx=(4, 0),
        )

        self._build_extrema_mapping_module(control_panel)

        options_frame = ttk.Labelframe(control_panel, text="图表设置", padding=12)
        options_frame.grid(row=7, column=0, sticky="ew")
        options_frame.columnconfigure(1, weight=1)
        options_frame.columnconfigure(3, weight=1)

        ttk.Label(options_frame, text="图表标题").grid(row=0, column=0, sticky="w", pady=(0, 8), padx=(0, 8))
        ttk.Entry(options_frame, textvariable=self.title_var).grid(row=0, column=1, columnspan=3, sticky="ew", pady=(0, 8))

        ttk.Label(options_frame, text="宽度(px)").grid(row=1, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(options_frame, textvariable=self.width_var, width=10).grid(row=1, column=1, sticky="ew")

        ttk.Label(options_frame, text="高度(px)").grid(row=1, column=2, sticky="w", padx=(12, 8))
        ttk.Entry(options_frame, textvariable=self.height_var, width=10).grid(row=1, column=3, sticky="ew")

        ttk.Label(options_frame, text="DPI").grid(row=2, column=0, sticky="w", pady=(8, 0), padx=(0, 8))
        ttk.Entry(options_frame, textvariable=self.dpi_var, width=10).grid(row=2, column=1, sticky="ew", pady=(8, 0))

        ttk.Label(options_frame, text="曲线线宽").grid(row=2, column=2, sticky="w", pady=(8, 0), padx=(12, 8))
        ttk.Entry(options_frame, textvariable=self.line_width_var, width=10).grid(row=2, column=3, sticky="ew", pady=(8, 0))

        ttk.Label(options_frame, text="时间刻度密度").grid(row=3, column=0, sticky="w", pady=(8, 0), padx=(0, 8))
        time_density_frame = ttk.Frame(options_frame)
        time_density_frame.grid(row=3, column=1, columnspan=3, sticky="ew", pady=(8, 0))
        time_density_frame.columnconfigure(0, weight=1)

        time_density_scale = tk.Scale(
            time_density_frame,
            from_=MIN_TIME_TICK_DENSITY,
            to=MAX_TIME_TICK_DENSITY,
            orient=tk.HORIZONTAL,
            resolution=1,
            showvalue=False,
            variable=self.time_density_var,
        )
        time_density_scale.grid(row=0, column=0, sticky="ew")
        ttk.Label(time_density_frame, textvariable=self.time_density_label_var).grid(row=0, column=1, sticky="e", padx=(8, 0))

        ttk.Label(options_frame, text="固定时间间隔").grid(row=4, column=0, sticky="w", pady=(8, 0), padx=(0, 8))
        fixed_interval_frame = ttk.Frame(options_frame)
        fixed_interval_frame.grid(row=4, column=1, columnspan=3, sticky="ew", pady=(8, 0))
        fixed_interval_frame.columnconfigure(0, weight=1)

        ttk.Entry(fixed_interval_frame, textvariable=self.fixed_time_interval_var).grid(row=0, column=0, sticky="ew")
        ttk.Combobox(
            fixed_interval_frame,
            textvariable=self.fixed_time_interval_unit_var,
            values=list(TIME_INTERVAL_UNIT_CHOICES.keys()),
            state="readonly",
            width=8,
        ).grid(row=0, column=1, sticky="ew", padx=(8, 0))

        ttk.Checkbutton(options_frame, text="显示网格", variable=self.show_grid_var).grid(
            row=5,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(8, 0),
        )
        ttk.Checkbutton(options_frame, text="显示图例", variable=self.show_legend_var).grid(
            row=5,
            column=2,
            columnspan=2,
            sticky="w",
            pady=(8, 0),
        )

        ttk.Checkbutton(options_frame, text="显示时间文字", variable=self.show_time_axis_var).grid(
            row=6,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(8, 0),
        )
        ttk.Checkbutton(options_frame, text="显示数值文字", variable=self.show_value_axis_var).grid(
            row=6,
            column=2,
            columnspan=2,
            sticky="w",
            pady=(8, 0),
        )

        ttk.Checkbutton(options_frame, text="纯曲线模式", variable=self.curve_only_mode_var).grid(
            row=7,
            column=0,
            columnspan=4,
            sticky="w",
            pady=(8, 0),
        )

        ttk.Label(options_frame, text="图例位置").grid(row=8, column=0, sticky="w", pady=(8, 0), padx=(0, 8))
        legend_location_box = ttk.Combobox(
            options_frame,
            textvariable=self.legend_location_var,
            values=list(LEGEND_LOCATION_CHOICES.keys()),
            state="readonly",
            width=10,
        )
        legend_location_box.grid(row=8, column=1, columnspan=3, sticky="ew", pady=(8, 0))

        self._build_chart_color_row(
            options_frame,
            row=9,
            label_text="坐标轴颜色",
            color_var=self.axis_color_var,
            field_name="坐标轴颜色",
        )
        self._build_chart_color_row(
            options_frame,
            row=10,
            label_text="网格颜色",
            color_var=self.grid_color_var,
            field_name="网格颜色",
        )
        self._build_chart_color_row(
            options_frame,
            row=11,
            label_text="时间文字颜色",
            color_var=self.time_text_color_var,
            field_name="时间文字颜色",
        )
        self._build_chart_color_row(
            options_frame,
            row=12,
            label_text="数值文字颜色",
            color_var=self.value_text_color_var,
            field_name="数值文字颜色",
        )
        self._build_chart_color_row(
            options_frame,
            row=13,
            label_text="图例文字颜色",
            color_var=self.legend_text_color_var,
            field_name="图例文字颜色",
        )

        ttk.Label(options_frame, text="字体").grid(row=14, column=0, sticky="w", pady=(8, 0), padx=(0, 8))
        ttk.Combobox(
            options_frame,
            textvariable=self.font_family_var,
            values=self.font_family_choices,
            state="readonly",
            height=20,
        ).grid(
            row=14,
            column=1,
            columnspan=3,
            sticky="ew",
            pady=(8, 0),
        )

        ttk.Label(options_frame, text="颜色支持 HEX 输入与取色器。").grid(
            row=15,
            column=0,
            columnspan=4,
            sticky="w",
            pady=(8, 0),
        )

        action_row = ttk.Frame(control_panel)
        action_row.grid(row=8, column=0, sticky="ew", pady=(12, 0))
        action_row.columnconfigure((0, 1), weight=1)

        ttk.Button(action_row, text="重置样式", command=self.reset_chart_options).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(action_row, text="导出透明 PNG", command=self.export_png).grid(row=0, column=1, sticky="ew", padx=(4, 0))

    def _build_extrema_mapping_module(self, parent: tk.Misc) -> None:
        extrema_frame = ttk.Labelframe(parent, text="峰谷映射", padding=12)
        extrema_frame.grid(row=6, column=0, sticky="nsew", pady=(0, 12))
        extrema_frame.columnconfigure(0, weight=1)
        extrema_frame.rowconfigure(3, weight=1)

        ttk.Checkbutton(
            extrema_frame,
            text="启用峰谷映射",
            variable=self.extrema_enabled_var,
        ).grid(row=0, column=0, sticky="w")

        source_row = ttk.Frame(extrema_frame)
        source_row.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        source_row.columnconfigure(1, weight=1)
        source_row.columnconfigure(3, weight=1)

        ttk.Label(source_row, text="检测源参数").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.extrema_source_combobox = ttk.Combobox(
            source_row,
            textvariable=self.extrema_source_var,
            state="readonly",
        )
        self.extrema_source_combobox.grid(row=0, column=1, sticky="ew")

        ttk.Label(source_row, text="模式").grid(row=0, column=2, sticky="w", padx=(12, 8))
        ttk.Combobox(
            source_row,
            textvariable=self.extrema_mode_var,
            values=list(EXTREMA_MODE_CHOICES.keys()),
            state="readonly",
            width=10,
        ).grid(row=0, column=3, sticky="ew")

        settings_row = ttk.Frame(extrema_frame)
        settings_row.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        settings_row.columnconfigure((1, 3, 5, 7), weight=1)

        ttk.Label(settings_row, text="最小间距(秒)").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(settings_row, textvariable=self.extrema_min_distance_var, width=8).grid(row=0, column=1, sticky="ew")
        ttk.Label(settings_row, text="最小突出度").grid(row=0, column=2, sticky="w", padx=(12, 8))
        ttk.Entry(settings_row, textvariable=self.extrema_min_prominence_var, width=8).grid(row=0, column=3, sticky="ew")
        ttk.Label(settings_row, text="平滑窗口").grid(row=0, column=4, sticky="w", padx=(12, 8))
        ttk.Entry(settings_row, textvariable=self.extrema_smoothing_window_var, width=8).grid(row=0, column=5, sticky="ew")
        ttk.Label(settings_row, text="分组容差(秒)").grid(row=0, column=6, sticky="w", padx=(12, 8))
        ttk.Entry(settings_row, textvariable=self.extrema_alignment_tolerance_var, width=8).grid(row=0, column=7, sticky="ew")

        action_row = ttk.Frame(extrema_frame)
        action_row.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        action_row.columnconfigure((0, 1, 2), weight=1)

        ttk.Button(action_row, text="重新检测", command=self.refresh_extrema_groups).grid(
            row=0,
            column=0,
            sticky="ew",
            padx=(0, 4),
        )
        ttk.Button(action_row, text="清空赋值", command=self.clear_extrema_assignments).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=4,
        )
        ttk.Button(action_row, text="清空峰谷点颜色", command=self.clear_extrema_point_colors).grid(
            row=0,
            column=2,
            sticky="ew",
            padx=(4, 0),
        )

        tree_frame = ttk.Frame(extrema_frame)
        tree_frame.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        self.extrema_group_tree = ttk.Treeview(
            tree_frame,
            columns=("group", "kind", "time", "file", "source", "assigned", "color"),
            show="headings",
            height=8,
        )
        self.extrema_group_tree.grid(row=0, column=0, sticky="nsew")
        self.extrema_group_tree.bind("<<TreeviewSelect>>", self.on_extrema_group_selection_changed)
        for column_name, heading_text, width in (
            ("group", "组号", 84),
            ("kind", "类型", 54),
            ("time", "对齐时间", 84),
            ("file", "文件", 120),
            ("source", "原始峰值", 90),
            ("assigned", "赋值", 90),
            ("color", "颜色", 90),
        ):
            self.extrema_group_tree.heading(column_name, text=heading_text)
            self.extrema_group_tree.column(column_name, width=width, anchor="center", stretch=column_name == "file")

        extrema_tree_scrollbar = ttk.Scrollbar(
            tree_frame,
            orient=tk.VERTICAL,
            command=self.extrema_group_tree.yview,
        )
        extrema_tree_scrollbar.grid(row=0, column=1, sticky="ns")
        self.extrema_group_tree.configure(yscrollcommand=extrema_tree_scrollbar.set)

        editor_frame = ttk.LabelFrame(extrema_frame, text="编辑", padding=10)
        editor_frame.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        editor_frame.columnconfigure(1, weight=1)
        editor_frame.columnconfigure(3, weight=1)

        ttk.Label(editor_frame, text="当前组号").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Label(editor_frame, textvariable=self.extrema_selected_group_var).grid(row=0, column=1, sticky="w")
        ttk.Label(editor_frame, text="当前文件").grid(row=0, column=2, sticky="w", padx=(12, 8))
        ttk.Label(editor_frame, textvariable=self.extrema_selected_file_var).grid(row=0, column=3, sticky="w")
        ttk.Label(editor_frame, text="原始峰值").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        ttk.Label(editor_frame, textvariable=self.extrema_selected_source_value_var).grid(row=1, column=1, sticky="w", pady=(8, 0))

        ttk.Label(editor_frame, text="赋值").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        ttk.Entry(editor_frame, textvariable=self.extrema_assignment_value_var).grid(row=2, column=1, sticky="ew", pady=(8, 0))
        ttk.Label(editor_frame, text="颜色HEX").grid(row=2, column=2, sticky="w", padx=(12, 8), pady=(8, 0))
        ttk.Entry(editor_frame, textvariable=self.extrema_point_color_var).grid(row=2, column=3, sticky="ew", pady=(8, 0))

        editor_button_row = ttk.Frame(editor_frame)
        editor_button_row.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        editor_button_row.columnconfigure((0, 1, 2, 3), weight=1)
        ttk.Button(editor_button_row, text="应用赋值", command=self.apply_extrema_assignment).grid(
            row=0,
            column=0,
            sticky="ew",
            padx=(0, 4),
        )
        ttk.Button(editor_button_row, text="清除赋值", command=self.clear_selected_extrema_assignment).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=4,
        )
        ttk.Button(editor_button_row, text="应用颜色", command=self.apply_extrema_point_color).grid(
            row=0,
            column=2,
            sticky="ew",
            padx=4,
        )
        ttk.Button(editor_button_row, text="清除颜色", command=self.clear_selected_extrema_point_color).grid(
            row=0,
            column=3,
            sticky="ew",
            padx=(4, 0),
        )

    def _build_preview_module(self, parent: tk.Misc) -> None:
        self.preview_module = ttk.Labelframe(parent, text="图表预览", padding=8)
        self.preview_module.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        self.preview_module.columnconfigure(0, weight=1)
        self.preview_module.rowconfigure(0, weight=1)

        self.preview_host = ttk.Frame(self.preview_module)
        self.preview_host.grid(row=0, column=0, sticky="nsew")
        self.preview_host.columnconfigure(0, weight=1)
        self.preview_host.rowconfigure(0, weight=1)
        self.preview_host.grid_propagate(False)
        self.preview_host.bind("<Configure>", self._on_preview_host_configure)

        self.preview_placeholder = ttk.Label(
            self.preview_host,
            text="添加或拖入一个或多个 CSV，并选择参数后，图表会按当前配置自动预览。",
            anchor="center",
            justify="center",
        )
        self.preview_placeholder.grid(row=0, column=0, sticky="nsew")

    def _build_time_editing_module(self, parent: tk.Misc) -> None:
        self.time_editing_module = ttk.Labelframe(parent, text="时间轴", padding=(10, 8, 10, 8))
        self.time_editing_module.grid(row=1, column=0, sticky="nsew")
        self.time_editing_module.columnconfigure(0, weight=1)
        self.time_editing_module.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self.time_editing_module)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        toolbar.columnconfigure(0, weight=1)

        ttk.Label(
            toolbar,
            text="时间轴默认自适应窗口；在时间轴上滚动鼠标滚轮可缩放细调。",
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(toolbar, text="重置所选对齐", command=self.reset_selected_session_offsets).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(8, 0),
        )
        ttk.Button(toolbar, text="重置所选裁剪", command=self.reset_selected_session_source_trims).grid(
            row=0,
            column=2,
            sticky="ew",
            padx=(8, 0),
        )
        timeline_host = ttk.Frame(self.time_editing_module)
        timeline_host.grid(row=1, column=0, sticky="nsew")
        timeline_host.columnconfigure(0, weight=1)
        timeline_host.rowconfigure(0, weight=1)

        self.timeline_canvas = tk.Canvas(
            timeline_host,
            height=170,
            background="#f7f8fb",
            highlightthickness=1,
            highlightbackground="#d6dbe6",
        )
        self.timeline_canvas.grid(row=0, column=0, sticky="nsew")
        self.timeline_canvas.bind("<Configure>", self._on_timeline_canvas_configure)
        self.timeline_canvas.bind("<ButtonPress-1>", self._on_timeline_button_press)
        self.timeline_canvas.bind("<B1-Motion>", self._on_timeline_drag)
        self.timeline_canvas.bind("<ButtonRelease-1>", self._on_timeline_button_release)

        self.timeline_hscrollbar = ttk.Scrollbar(
            timeline_host,
            orient=tk.HORIZONTAL,
            command=self.timeline_canvas.xview,
        )
        self.timeline_hscrollbar.grid(row=1, column=0, sticky="ew")
        self.timeline_canvas.configure(xscrollcommand=self.timeline_hscrollbar.set)

        ttk.Label(self.time_editing_module, textvariable=self.timeline_status_var).grid(row=2, column=0, sticky="w", pady=(8, 0))

    def _find_default_csv(self) -> str:
        for pattern in ("*.csv", "*.CSV"):
            matches = list(Path.cwd().glob(pattern))
            if matches:
                return str(matches[0])
        return ""

    @staticmethod
    def normalize_session_path(file_path: str | Path) -> str:
        return str(Path(file_path).expanduser().resolve())

    @staticmethod
    def format_offset_seconds(value: float) -> str:
        if float(value).is_integer():
            return f"{int(value)}"
        return f"{value:.3f}".rstrip("0").rstrip(".")

    @staticmethod
    def _format_log_float(value: float) -> str:
        return f"{float(value):.3f}"

    def _format_session_log_summary(self, session: LoadedCsvSession) -> str:
        trim_start_seconds, trim_end_seconds = resolve_session_source_trim_range(session)
        session_alias = session.alias.strip() or session.data.source_path.stem
        return (
            f"{session_alias}(id={session.session_id},file={session.data.source_path.name},"
            f"reference={session.is_reference},visible={session.is_visible},"
            f"offset={self._format_log_float(session.offset_seconds)},"
            f"trim={self._format_log_float(trim_start_seconds)}->{self._format_log_float(trim_end_seconds)},"
            f"preload_ready={session.preload_ready})"
        )

    def _summarize_sessions_for_log(
        self,
        sessions: Sequence[LoadedCsvSession] | None = None,
        *,
        limit: int = 6,
    ) -> str:
        source_sessions = list(self.sessions if sessions is None else sessions)
        if not source_sessions:
            return "-"

        parts = [self._format_session_log_summary(session) for session in source_sessions[:limit]]
        if len(source_sessions) > limit:
            parts.append(f"...(+{len(source_sessions) - limit} more)")
        return "; ".join(parts)

    def _summarize_series_keys_for_log(
        self,
        series_keys: Sequence[SeriesKey],
        *,
        limit: int = 6,
    ) -> str:
        if not series_keys:
            return "-"

        parts: list[str] = []
        for series_key in series_keys[:limit]:
            session = self.get_session_by_id(series_key.session_id)
            session_alias = series_key.session_id
            column_label = str(series_key.column_index)
            if session is not None:
                session_alias = session.alias.strip() or session.data.source_path.stem
                try:
                    column_label = session.data.column_for_index(series_key.column_index).name
                except KeyError:
                    column_label = str(series_key.column_index)
            parts.append(f"{session_alias}:{column_label}[{series_key.column_index}]")

        if len(series_keys) > limit:
            parts.append(f"...(+{len(series_keys) - limit} more)")
        return "; ".join(parts)

    def log_comparison_structure(self, reason: str) -> None:
        render_sessions = self.get_render_sessions()
        shared_parameter_columns = self.get_shared_parameter_columns()
        selected_series = self.get_selected_series_keys()
        logger.info(
            (
                "Comparison structure refreshed reason=%s total_sessions=%d render_sessions=%d "
                "shared_parameters=%d selected_series=%d sessions_detail=%s selected_detail=%s"
            ),
            reason,
            len(self.sessions),
            len(render_sessions),
            len(shared_parameter_columns),
            len(selected_series),
            self._summarize_sessions_for_log(),
            self._summarize_series_keys_for_log(selected_series),
        )

    def _build_session_id(self) -> str:
        return uuid4().hex

    def get_render_sessions(self) -> tuple[LoadedCsvSession, ...]:
        return tuple(session for session in self.sessions if session.is_visible)

    def get_session_by_id(self, session_id: str) -> LoadedCsvSession | None:
        for session in self.sessions:
            if session.session_id == session_id:
                return session
        return None

    def get_selected_session_ids(self) -> tuple[str, ...]:
        return tuple(self.session_tree.selection())

    def get_selected_session(self) -> LoadedCsvSession | None:
        selected_session_ids = self.get_selected_session_ids()
        if len(selected_session_ids) != 1:
            return None
        return self.get_session_by_id(selected_session_ids[0])

    def get_shared_parameter_columns(self) -> list[SensorColumn]:
        render_sessions = self.get_render_sessions()
        if not render_sessions:
            return []

        first_session = render_sessions[0]
        if len(render_sessions) == 1:
            return list(first_session.data.columns)

        shared_parameter_keys = {column.shared_key for column in first_session.data.columns}
        for session in render_sessions[1:]:
            shared_parameter_keys &= {column.shared_key for column in session.data.columns}

        return [
            column
            for column in first_session.data.columns
            if column.shared_key in shared_parameter_keys
        ]

    def get_series_column(self, series_key: SeriesKey) -> SensorColumn | None:
        session = self.get_session_by_id(series_key.session_id)
        if session is None:
            return None
        try:
            return session.data.column_for_index(series_key.column_index)
        except KeyError:
            return None

    def get_selected_parameter_shared_keys(self) -> tuple[str, ...]:
        selected_parameter_keys: set[str] = set()
        for series_key in self.selected_series_keys:
            column = self.get_series_column(series_key)
            if column is not None:
                selected_parameter_keys.add(column.shared_key)

        return tuple(
            column.shared_key
            for column in self.get_shared_parameter_columns()
            if column.shared_key in selected_parameter_keys
        )

    def expand_series_keys_for_parameter_shared_keys(self, parameter_shared_keys: Sequence[str]) -> set[SeriesKey]:
        parameter_key_set = set(parameter_shared_keys)
        if not parameter_key_set:
            return set()

        expanded_series_keys: set[SeriesKey] = set()
        for session in self.get_render_sessions():
            for parameter_key in parameter_key_set:
                column = session.data.find_column_by_shared_key(parameter_key)
                if column is not None:
                    expanded_series_keys.add(SeriesKey(session.session_id, column.index))
        return expanded_series_keys

    def sync_selected_parameter_series(self) -> None:
        if not self.sessions:
            self.selected_series_keys.clear()
            return

        shared_parameter_keys = {column.shared_key for column in self.get_shared_parameter_columns()}
        selected_parameter_keys: set[str] = set()
        for series_key in self.selected_series_keys:
            column = self.get_series_column(series_key)
            if column is not None and column.shared_key in shared_parameter_keys:
                selected_parameter_keys.add(column.shared_key)

        self.selected_series_keys = self.expand_series_keys_for_parameter_shared_keys(selected_parameter_keys)

    def refresh_session_tree(self, preferred_selection: Sequence[str] | None = None) -> None:
        selected_session_ids = tuple(preferred_selection) if preferred_selection is not None else self.get_selected_session_ids()
        self.session_tree.delete(*self.session_tree.get_children())

        for session in self.sessions:
            duration_text = "00:00:00"
            if session.data.elapsed_seconds:
                duration_text = format_elapsed_time(session.data.elapsed_seconds[-1])
            self.session_tree.insert(
                "",
                tk.END,
                iid=session.session_id,
                values=(
                    session.data.source_path.name,
                    session.alias,
                    duration_text,
                ),
            )

        valid_selection_ids = [session_id for session_id in selected_session_ids if self.session_tree.exists(session_id)]
        if not valid_selection_ids and self.sessions:
            valid_selection_ids = [self.sessions[0].session_id]

        if valid_selection_ids:
            self.session_tree.selection_set(valid_selection_ids)
            self.session_tree.focus(valid_selection_ids[0])
        else:
            self.session_tree.selection_remove(self.session_tree.selection())

        self.sync_session_editor()

    def sync_session_editor(self) -> None:
        selected_session = self.get_selected_session()

        self._updating_session_editor = True
        try:
            self.session_alias_var.set("" if selected_session is None else selected_session.alias)
            self.session_offset_var.set(
                "" if selected_session is None else self.format_offset_seconds(selected_session.offset_seconds)
            )
            if selected_session is None:
                self.session_source_trim_var.set("有效片段：--")
            else:
                trim_start_seconds, trim_end_seconds = resolve_session_source_trim_range(selected_session)
                self.session_source_trim_var.set(
                    f"有效片段：{format_elapsed_time(trim_start_seconds)} → {format_elapsed_time(trim_end_seconds)}"
                )
        finally:
            self._updating_session_editor = False
        if self.session_alias_entry is not None:
            self.session_alias_entry.configure(state=tk.NORMAL if selected_session is not None else tk.DISABLED)
        if self.session_offset_entry is not None:
            self.session_offset_entry.configure(state=tk.NORMAL if selected_session is not None else tk.DISABLED)

    def on_session_selection_changed(self, _event=None) -> None:
        self.sync_session_editor()
        self.refresh_timeline()

    def refresh_after_session_change(
        self,
        *,
        preferred_selection: Sequence[str] | None = None,
        preserve_trim_range: bool = True,
        refresh_preview: bool = True,
        reason: str = "session_change",
    ) -> None:
        self.refresh_session_tree(preferred_selection=preferred_selection)
        self.sync_selected_parameter_series()
        self.refresh_extrema_source_options()
        self.refresh_column_list()
        self.refresh_selected_series_list()
        self.refresh_extrema_groups()
        self.configure_trim_controls(preserve_range=preserve_trim_range)
        self.log_comparison_structure(reason)
        if refresh_preview:
            self.schedule_preview_refresh(immediate=True)

    def _on_extrema_enabled_changed(self, *_args) -> None:
        if self._updating_extrema_controls:
            return
        self.refresh_extrema_source_options()
        self.refresh_extrema_groups()
        self.schedule_preview_refresh(immediate=True)

    def _on_extrema_setting_changed(self, *_args) -> None:
        if self._updating_extrema_controls:
            return
        self.refresh_extrema_groups()
        self.schedule_preview_refresh(immediate=True)

    def refresh_extrema_source_options(self) -> None:
        self.extrema_source_columns = self.get_shared_parameter_columns()
        source_labels = [column.display_name for column in self.extrema_source_columns]
        current_source_label = self.extrema_source_var.get().strip()
        next_source_label = current_source_label if current_source_label in source_labels else ""
        if not next_source_label and source_labels:
            next_source_label = source_labels[0]

        self._updating_extrema_controls = True
        try:
            if self.extrema_source_combobox is not None:
                self.extrema_source_combobox.configure(values=source_labels)
                self.extrema_source_combobox.configure(
                    state="readonly" if self.extrema_enabled_var.get() and source_labels else tk.DISABLED
                )
            self.extrema_source_var.set(next_source_label)
        finally:
            self._updating_extrema_controls = False

    def get_selected_extrema_source_column(self) -> SensorColumn | None:
        selected_label = self.extrema_source_var.get().strip()
        for column in self.extrema_source_columns:
            if column.display_name == selected_label:
                return column
        return None

    def build_extrema_detection_config(self) -> ExtremaDetectionConfig | None:
        if not self.extrema_enabled_var.get():
            return None

        source_column = self.get_selected_extrema_source_column()
        if source_column is None:
            return None

        render_sessions = self.get_render_sessions()
        source_series_keys = tuple(
            SeriesKey(session.session_id, column.index)
            for session in render_sessions
            for column in (session.data.find_column_by_shared_key(source_column.shared_key),)
            if column is not None
        )
        if not source_series_keys:
            return None

        return ExtremaDetectionConfig(
            enabled=True,
            source_series_keys=source_series_keys,
            mode=EXTREMA_MODE_CHOICES.get(self.extrema_mode_var.get(), "both"),
            min_distance_seconds=self.parse_nonnegative_float(self.extrema_min_distance_var.get(), "最小间距(秒)"),
            min_prominence=self.parse_nonnegative_float(self.extrema_min_prominence_var.get(), "最小突出度"),
            smoothing_window=self.parse_min_int(self.extrema_smoothing_window_var.get(), "平滑窗口", minimum=1),
            alignment_tolerance_seconds=self.parse_nonnegative_float(
                self.extrema_alignment_tolerance_var.get(),
                "分组容差(秒)",
            ),
            use_secondary_axis=True,
        )

    def get_selected_extrema_point_key(self) -> ExtremaPointKey | None:
        if self.extrema_group_tree is None:
            return None
        selected_rows = self.extrema_group_tree.selection()
        if len(selected_rows) != 1:
            return None
        return self.extrema_point_key_by_row_id.get(selected_rows[0])

    def get_extrema_point_context(
        self,
        point_key: ExtremaPointKey,
    ) -> tuple[AlignedExtremaGroup, object] | None:
        for group in self.detected_extrema_groups:
            if group.group_id != point_key.group_id:
                continue
            for member in group.members:
                if member.key == point_key.key:
                    return group, member
        return None

    def refresh_extrema_groups(self) -> None:
        preferred_point_key = self.get_selected_extrema_point_key() or self.selected_extrema_point_key
        try:
            extrema_config = self.build_extrema_detection_config()
        except ValueError as exc:
            logger.exception("Failed to refresh extrema groups while building config")
            self.detected_extrema_groups = ()
            self.extrema_assignments.clear()
            self.extrema_point_colors.clear()
            self.refresh_extrema_group_tree(preferred_point_key=None)
            self.status_var.set(f"峰谷重算失败：{exc}")
            return

        if extrema_config is None:
            self.detected_extrema_groups = ()
            self.extrema_assignments.clear()
            self.extrema_point_colors.clear()
            self.refresh_extrema_group_tree(preferred_point_key=None)
            logger.debug("Extrema mapping disabled; cleared detected groups")
            return

        detected_extrema = detect_extrema_for_sessions(self.get_render_sessions(), extrema_config)
        self.detected_extrema_groups = group_aligned_extrema(
            detected_extrema,
            alignment_tolerance_seconds=extrema_config.alignment_tolerance_seconds,
            reference_session_id=next(
                (session.session_id for session in self.get_render_sessions() if session.is_reference),
                None,
            ),
        )
        valid_point_keys = {
            ExtremaPointKey(group.group_id, member.key)
            for group in self.detected_extrema_groups
            for member in group.members
        }
        self.extrema_assignments = {
            point_key: assigned_value
            for point_key, assigned_value in self.extrema_assignments.items()
            if point_key in valid_point_keys
        }
        self.extrema_point_colors = {
            point_key: color_text
            for point_key, color_text in self.extrema_point_colors.items()
            if point_key in valid_point_keys
        }
        self.refresh_extrema_group_tree(preferred_point_key=preferred_point_key)
        logger.info(
            (
                "Extrema groups refreshed groups=%d source=%s mode=%s assignments=%d "
                "point_colors=%d alignment_tolerance=%.3f"
            ),
            len(self.detected_extrema_groups),
            self.extrema_source_var.get().strip() or "<none>",
            extrema_config.mode,
            len(self.extrema_assignments),
            len(self.extrema_point_colors),
            float(extrema_config.alignment_tolerance_seconds),
        )

    def refresh_extrema_group_tree(self, *, preferred_point_key: ExtremaPointKey | None) -> None:
        if self.extrema_group_tree is None:
            return

        self.extrema_group_tree.delete(*self.extrema_group_tree.get_children())
        self.extrema_point_key_by_row_id.clear()
        selected_row_id: str | None = None

        for group in self.detected_extrema_groups:
            for member in group.members:
                point_key = ExtremaPointKey(group.group_id, member.key)
                row_id = f"{group.group_id}|{member.key.session_id}|{member.key.column_index}"
                self.extrema_point_key_by_row_id[row_id] = point_key
                session = self.get_session_by_id(member.key.session_id)
                session_alias = session.alias if session is not None else member.key.session_id
                assigned_value = self.extrema_assignments.get(point_key)
                color_text = self.extrema_point_colors.get(point_key, "")
                self.extrema_group_tree.insert(
                    "",
                    tk.END,
                    iid=row_id,
                    values=(
                        group.group_id,
                        EXTREMA_KIND_LABELS.get(group.kind, group.kind),
                        format_compact_elapsed_time(group.anchor_seconds),
                        session_alias,
                        f"{float(member.source_value):.3f}",
                        "" if assigned_value is None else f"{float(assigned_value):.3f}",
                        color_text.upper(),
                    ),
                )
                if preferred_point_key == point_key:
                    selected_row_id = row_id

        if selected_row_id is not None and self.extrema_group_tree.exists(selected_row_id):
            self.extrema_group_tree.selection_set(selected_row_id)
            self.extrema_group_tree.focus(selected_row_id)
        elif self.extrema_group_tree.selection():
            self.extrema_group_tree.selection_remove(self.extrema_group_tree.selection())

        self.sync_extrema_editor()

    def on_extrema_group_selection_changed(self, _event=None) -> None:
        self.sync_extrema_editor()

    def sync_extrema_editor(self) -> None:
        point_key = self.get_selected_extrema_point_key()
        self.selected_extrema_point_key = point_key
        if point_key is None:
            self.extrema_selected_group_var.set("--")
            self.extrema_selected_file_var.set("--")
            self.extrema_selected_source_value_var.set("--")
            self.extrema_assignment_value_var.set("")
            self.extrema_point_color_var.set("")
            return

        context = self.get_extrema_point_context(point_key)
        if context is None:
            self.extrema_selected_group_var.set("--")
            self.extrema_selected_file_var.set("--")
            self.extrema_selected_source_value_var.set("--")
            self.extrema_assignment_value_var.set("")
            self.extrema_point_color_var.set("")
            return

        group, member = context
        session = self.get_session_by_id(member.key.session_id)
        session_alias = session.alias if session is not None else member.key.session_id
        self.extrema_selected_group_var.set(group.group_id)
        self.extrema_selected_file_var.set(session_alias)
        self.extrema_selected_source_value_var.set(f"{float(member.source_value):.3f}")
        assigned_value = self.extrema_assignments.get(point_key)
        self.extrema_assignment_value_var.set("" if assigned_value is None else f"{float(assigned_value):.3f}")
        self.extrema_point_color_var.set(self.extrema_point_colors.get(point_key, "").removeprefix("#").upper())

    def apply_extrema_assignment(self) -> None:
        point_key = self.get_selected_extrema_point_key()
        if point_key is None:
            messagebox.showinfo("未选择峰谷点", "请先在峰谷映射列表中选择一个峰谷点。")
            return

        try:
            assigned_value = self.parse_float(self.extrema_assignment_value_var.get(), "赋值")
        except ValueError as exc:
            messagebox.showerror("赋值格式无效", str(exc))
            self.sync_extrema_editor()
            return

        self.extrema_assignments[point_key] = assigned_value
        self.refresh_extrema_group_tree(preferred_point_key=point_key)
        self.schedule_preview_refresh(immediate=True)

    def clear_selected_extrema_assignment(self) -> None:
        point_key = self.get_selected_extrema_point_key()
        if point_key is None:
            return

        if self.extrema_assignments.pop(point_key, None) is None:
            return

        self.refresh_extrema_group_tree(preferred_point_key=point_key)
        self.schedule_preview_refresh(immediate=True)

    def clear_extrema_assignments(self) -> None:
        if not self.extrema_assignments:
            return

        preferred_point_key = self.get_selected_extrema_point_key()
        self.extrema_assignments.clear()
        self.refresh_extrema_group_tree(preferred_point_key=preferred_point_key)
        self.schedule_preview_refresh(immediate=True)

    def apply_extrema_point_color(self) -> None:
        point_key = self.get_selected_extrema_point_key()
        if point_key is None:
            messagebox.showinfo("未选择峰谷点", "请先在峰谷映射列表中选择一个峰谷点。")
            return

        try:
            selected_color = self.normalize_hex_color(self.extrema_point_color_var.get())
        except ValueError as exc:
            messagebox.showerror("颜色格式无效", str(exc))
            self.sync_extrema_editor()
            return

        self.extrema_point_color_var.set(selected_color.removeprefix("#").upper())
        self.extrema_point_colors[point_key] = selected_color
        self.refresh_extrema_group_tree(preferred_point_key=point_key)
        self.schedule_preview_refresh(immediate=True)

    def clear_selected_extrema_point_color(self) -> None:
        point_key = self.get_selected_extrema_point_key()
        if point_key is None:
            return

        if self.extrema_point_colors.pop(point_key, None) is None:
            return

        self.refresh_extrema_group_tree(preferred_point_key=point_key)
        self.schedule_preview_refresh(immediate=True)

    def clear_extrema_point_colors(self) -> None:
        if not self.extrema_point_colors:
            return

        preferred_point_key = self.get_selected_extrema_point_key()
        self.extrema_point_colors.clear()
        self.refresh_extrema_group_tree(preferred_point_key=preferred_point_key)
        self.schedule_preview_refresh(immediate=True)

    def add_csv_files(self, file_paths: Sequence[str]) -> None:
        logger.info("Adding CSV files requested_paths=%s", list(file_paths))
        normalized_paths: list[str] = []
        seen_paths: set[str] = set()
        for file_path in file_paths:
            if Path(file_path).suffix.lower() != ".csv":
                continue
            normalized_path = self.normalize_session_path(file_path)
            if normalized_path in seen_paths:
                continue
            seen_paths.add(normalized_path)
            normalized_paths.append(normalized_path)

        if not normalized_paths:
            logger.warning("No CSV files found in requested_paths=%s", list(file_paths))
            messagebox.showerror("未找到 CSV", "请选择或拖入至少一个 .csv / .CSV 文件。")
            return

        existing_paths = {
            self.normalize_session_path(session.data.source_path): session.session_id
            for session in self.sessions
        }
        new_sessions: list[LoadedCsvSession] = []
        skipped_paths: list[str] = []
        failed_messages: list[str] = []
        had_existing_sessions = bool(self.sessions)

        self.cancel_pending_preview_requests()
        self.status_var.set("正在加载 CSV 并整理可用参数，请稍候...")
        self.update_idletasks()
        logger.info(
            "Normalized CSV paths count=%d existing_sessions=%d paths=%s",
            len(normalized_paths),
            len(self.sessions),
            normalized_paths,
        )

        for normalized_path in normalized_paths:
            existing_session_id = existing_paths.get(normalized_path)
            if existing_session_id is not None:
                skipped_paths.append(Path(normalized_path).name)
                logger.info(
                    "Skipping duplicate CSV path=%s existing_session_id=%s",
                    normalized_path,
                    existing_session_id,
                )
                continue

            try:
                data = load_hwinfo_csv(normalized_path, preload_numeric=False)
            except Exception as exc:
                logger.exception("Failed to add CSV path=%s", normalized_path)
                failed_messages.append(f"{Path(normalized_path).name}：{exc}")
                continue

            new_session = LoadedCsvSession(
                session_id=self._build_session_id(),
                alias=data.source_path.stem,
                data=data,
                is_reference=not self.sessions and not new_sessions,
            )
            new_sessions.append(new_session)
            existing_paths[normalized_path] = new_session.session_id

        if new_sessions:
            self.sessions.extend(new_sessions)
            if not any(session.is_reference for session in self.sessions):
                self.sessions = list(normalize_offsets_for_reference(self.sessions, self.sessions[0].session_id))

            for session in new_sessions:
                self.start_background_preload(session)

            self.refresh_after_session_change(
                preferred_selection=[session.session_id for session in new_sessions],
                preserve_trim_range=had_existing_sessions,
                reason="add_csv_files",
            )
            logger.info(
                "Added CSV files new_sessions=%d total_sessions=%d skipped=%d failed=%d",
                len(new_sessions),
                len(self.sessions),
                len(skipped_paths),
                len(failed_messages),
            )
            self.status_var.set(
                f"已加载 {len(new_sessions)} 个 CSV 文件，当前共 {len(self.sessions)} 个文件，"
                f"{len(build_series_descriptors(self.get_render_sessions()))} 个可选参数。"
            )
        elif skipped_paths and not failed_messages:
            self.refresh_session_tree()
            logger.info("All requested CSV files were duplicates skipped=%d", len(skipped_paths))
            self.status_var.set("选中的 CSV 已存在，已跳过重复导入。")
        elif failed_messages:
            logger.warning("CSV loading failed for all requested files count=%d", len(failed_messages))
            self.status_var.set("CSV 加载失败。")

        if failed_messages:
            messagebox.showerror("部分 CSV 加载失败", "\n".join(failed_messages))
        elif skipped_paths and new_sessions:
            self.status_var.set(
                f"已加载 {len(new_sessions)} 个 CSV 文件，跳过 {len(skipped_paths)} 个重复文件。"
            )

    def remove_selected_sessions(self) -> None:
        selected_session_ids = set(self.get_selected_session_ids())
        if not selected_session_ids:
            return

        logger.info("Removing selected sessions session_ids=%s", sorted(selected_session_ids))
        self.sessions = [session for session in self.sessions if session.session_id not in selected_session_ids]
        self.selected_series_keys = {
            series_key
            for series_key in self.selected_series_keys
            if series_key.session_id not in selected_session_ids
        }
        self.series_colors = {
            series_key: color_text
            for series_key, color_text in self.series_colors.items()
            if series_key.session_id not in selected_session_ids
        }

        if self.sessions and not any(session.is_reference for session in self.sessions):
            self.sessions = list(normalize_offsets_for_reference(self.sessions, self.sessions[0].session_id))

        if not self.sessions:
            self.cancel_pending_preview_requests()
            self.cancel_pending_preload_requests()
            self.refresh_after_session_change(
                preferred_selection=(),
                preserve_trim_range=False,
                refresh_preview=False,
                reason="remove_selected_sessions",
            )
            self.clear_preview()
            self.status_var.set("请添加一个或多个 HWiNFO CSV 文件。")
            return

        self.refresh_after_session_change(
            preferred_selection=[self.sessions[0].session_id],
            preserve_trim_range=True,
            reason="remove_selected_sessions",
        )
        self.status_var.set("已移除选中的 CSV 文件。")

    def clear_all_sessions(self) -> None:
        logger.info("Clearing all sessions total_sessions=%d", len(self.sessions))
        self.sessions.clear()
        self.visible_series_descriptors.clear()
        self.selected_series_keys.clear()
        self.series_colors.clear()
        self.cancel_pending_preview_requests()
        self.cancel_pending_preload_requests()
        self.refresh_after_session_change(
            preferred_selection=(),
            preserve_trim_range=False,
            refresh_preview=False,
            reason="clear_all_sessions",
        )
        self.clear_preview()
        self.status_var.set("请添加一个或多个 HWiNFO CSV 文件。")

    def set_selected_session_as_reference(self) -> None:
        selected_session = self.get_selected_session()
        if selected_session is None:
            messagebox.showinfo("未选择文件", "请在文件列表中选中一个 CSV 文件并将其设为基准。")
            return

        self.sessions = list(normalize_offsets_for_reference(self.sessions, selected_session.session_id))
        self.refresh_after_session_change(
            preferred_selection=[selected_session.session_id],
            preserve_trim_range=True,
            reason="set_selected_session_as_reference",
        )
        logger.info(
            "Set selected session as reference session_id=%s alias=%s",
            selected_session.session_id,
            selected_session.alias,
        )
        self.status_var.set(f"已将 {selected_session.alias} 设为基准文件。")

    def apply_selected_session_alias(self, _event=None) -> str | None:
        if self._updating_session_editor:
            return None

        selected_session = self.get_selected_session()
        if selected_session is None:
            return None

        alias = self.session_alias_var.get().strip() or selected_session.data.source_path.stem
        self.sessions = [
            replace(session, alias=alias) if session.session_id == selected_session.session_id else session
            for session in self.sessions
        ]
        self.refresh_after_session_change(
            preferred_selection=[selected_session.session_id],
            preserve_trim_range=True,
            reason="apply_selected_session_alias",
        )
        logger.info(
            "Updated session alias session_id=%s alias=%s",
            selected_session.session_id,
            alias,
        )
        self.status_var.set(f"已更新 {alias} 的别名。")
        return "break"

    def apply_selected_session_details(self, _event=None) -> str | None:
        if self._updating_session_editor:
            return None

        selected_session = self.get_selected_session()
        if selected_session is None:
            return None

        try:
            offset_seconds = self.parse_float(self.session_offset_var.get(), "偏移(秒)")
        except ValueError as exc:
            messagebox.showerror("偏移格式无效", str(exc))
            self.sync_session_editor()
            return "break"

        alias = self.session_alias_var.get().strip() or selected_session.data.source_path.stem
        updated_sessions: list[LoadedCsvSession] = []
        for session in self.sessions:
            if session.session_id == selected_session.session_id:
                updated_sessions.append(
                    replace(
                        session,
                        alias=alias,
                        offset_seconds=offset_seconds,
                    )
                )
            else:
                updated_sessions.append(session)

        self.sessions = updated_sessions
        self.refresh_after_session_change(
            preferred_selection=[selected_session.session_id],
            preserve_trim_range=True,
            reason="apply_selected_session_details",
        )
        logger.info(
            "Updated session details session_id=%s alias=%s offset_seconds=%.3f",
            selected_session.session_id,
            alias,
            float(offset_seconds),
        )
        self.status_var.set(f"已更新 {alias} 的别名和偏移。")
        return "break"

    def nudge_selected_session_offset(self, delta_seconds: float) -> None:
        selected_session = self.get_selected_session()
        if selected_session is None:
            messagebox.showinfo("未选择文件", "请先在文件列表中选中一个 CSV 文件。")
            return

        self.session_offset_var.set(
            self.format_offset_seconds(float(selected_session.offset_seconds) + float(delta_seconds))
        )
        self.apply_selected_session_details()

    def format_series_list_label(self, descriptor: SeriesDescriptor) -> str:
        if len(self.get_render_sessions()) > 1:
            return f"[{descriptor.session_alias}] {descriptor.column_display_name}"
        return descriptor.column_display_name

    def _on_filter_changed(self, *_args) -> None:
        self.refresh_column_list()

    def _bind_mousewheel_events(self) -> None:
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind_all(sequence, self._on_mousewheel, add="+")

    def _on_mousewheel(self, event) -> str | None:
        units = self._get_mousewheel_units(event)
        if units == 0:
            return None

        if event.widget is self.timeline_canvas:
            return self._on_timeline_mousewheel(event, units)

        horizontal = bool(getattr(event, "state", 0) & 0x0001)
        scroll_command = self._resolve_mousewheel_scroll_command(event.widget, horizontal=horizontal)
        if scroll_command is None and horizontal:
            scroll_command = self._resolve_mousewheel_scroll_command(event.widget, horizontal=False)
        if scroll_command is None:
            return None

        scroll_command(units, "units")
        return "break"

    @staticmethod
    def _get_mousewheel_units(event) -> int:
        delta = getattr(event, "delta", 0)
        if delta:
            direction = -1 if delta > 0 else 1
            magnitude = max(1, abs(int(delta)) // 120)
            return direction * magnitude

        event_num = getattr(event, "num", None)
        if event_num == 4:
            return -1
        if event_num == 5:
            return 1
        return 0

    def _resolve_mousewheel_scroll_command(self, widget, *, horizontal: bool):
        if widget is self.column_listbox:
            return None if horizontal else self.column_listbox.yview_scroll
        if widget is self.selected_series_listbox:
            return None if horizontal else self.selected_series_listbox.yview_scroll

        if self._widget_is_descendant_of(widget, self.control_panel) or widget is self.control_scroll_canvas:
            return None if horizontal else self.control_scroll_canvas.yview_scroll

        return None

    @staticmethod
    def _widget_is_descendant_of(widget, ancestor) -> bool:
        current_widget = widget
        while current_widget is not None:
            if current_widget is ancestor:
                return True
            current_widget = getattr(current_widget, "master", None)
        return False

    def _on_chart_option_changed(self, *_args) -> None:
        if self._suppress_chart_option_refresh:
            return
        self.schedule_preview_refresh()

    def _on_standard_chart_element_changed(self, *_args) -> None:
        if not self._updating_curve_only_mode and self.curve_only_mode_var.get() and self.has_enabled_chart_elements():
            self._updating_curve_only_mode = True
            try:
                self.curve_only_mode_var.set(False)
            finally:
                self._updating_curve_only_mode = False

        self.schedule_preview_refresh()

    def _on_curve_only_mode_changed(self, *_args) -> None:
        if self.curve_only_mode_var.get() and not self._updating_curve_only_mode:
            self._updating_curve_only_mode = True
            try:
                self.show_grid_var.set(False)
                self.show_legend_var.set(False)
                self.show_time_axis_var.set(False)
                self.show_value_axis_var.set(False)
            finally:
                self._updating_curve_only_mode = False

        self.schedule_preview_refresh()

    def has_enabled_chart_elements(self) -> bool:
        return (
            self.show_grid_var.get()
            or self.show_legend_var.get()
            or self.show_time_axis_var.get()
            or self.show_value_axis_var.get()
        )

    def _on_time_density_changed(self, *_args) -> None:
        self.update_time_density_label()
        self.schedule_preview_refresh()

    def update_time_density_label(self) -> None:
        density_value = self.parse_time_tick_density()
        self.time_density_label_var.set(f"{density_value}")

    def configure_trim_controls(self, preserve_range: bool = False) -> None:
        _ = preserve_range
        self.refresh_timeline()

    def get_global_time_bounds(self) -> tuple[float, float]:
        return compute_global_time_bounds(self.get_render_sessions())

    def get_visible_range_seconds(self) -> tuple[float, float] | None:
        return None

    def _on_control_host_configure(self, _event=None) -> None:
        scroll_region = self.control_scroll_canvas.bbox("all")
        if scroll_region is None:
            scroll_region = (0, 0, 0, 0)
        self.control_scroll_canvas.configure(scrollregion=scroll_region)

    def _on_control_canvas_configure(self, event=None) -> None:
        if event is None:
            return
        self.control_scroll_canvas.itemconfigure(self.control_window_id, width=event.width)

    def _on_preview_host_configure(self, _event=None) -> None:
        if self.preview_source_png_bytes is None:
            return
        self.schedule_preview_display_refresh()

    def _on_timeline_canvas_configure(self, _event=None) -> None:
        self.refresh_timeline()

    def _on_timeline_mousewheel(self, event, units: int) -> str:
        if not self.get_render_sessions():
            return "break"

        zoom_multiplier = 1.12 if units < 0 else 1 / 1.12
        updated_zoom_factor = min(8.0, max(1.0, self.timeline_zoom_factor * zoom_multiplier))
        if abs(updated_zoom_factor - self.timeline_zoom_factor) < 1e-9:
            return "break"

        anchor_canvas_x = self.timeline_canvas.canvasx(event.x)
        anchor_seconds = self._timeline_x_to_seconds(anchor_canvas_x)
        self.timeline_zoom_factor = updated_zoom_factor
        self.refresh_timeline()
        self._restore_timeline_anchor(anchor_seconds, event.x)
        return "break"

    def _restore_timeline_anchor(self, anchor_seconds: float, viewport_x: float) -> None:
        if not self.get_render_sessions():
            return

        scroll_region = self.timeline_canvas.cget("scrollregion")
        if not scroll_region:
            return

        try:
            _x1, _y1, x2, _y2 = [float(value) for value in str(scroll_region).split()]
        except ValueError:
            return

        viewport_width = max(float(self.timeline_canvas.winfo_width()), 1.0)
        target_left_x = self._timeline_seconds_to_x(anchor_seconds) - float(viewport_x)
        max_left_x = max(0.0, x2 - viewport_width)
        clamped_left_x = min(max(0.0, target_left_x), max_left_x)
        if x2 <= 0:
            return

        self.timeline_canvas.xview_moveto(clamped_left_x / x2)

    def get_timeline_bounds(self) -> tuple[float, float]:
        render_sessions = self.get_render_sessions()
        if not render_sessions:
            return 0.0, 60.0

        start_candidates: list[float] = []
        end_candidates: list[float] = []
        for session in render_sessions:
            full_start_seconds, full_end_seconds = compute_session_timeline_range(session)
            active_start_seconds, active_end_seconds = compute_session_active_timeline_range(session)
            start_candidates.extend((full_start_seconds, active_start_seconds))
            end_candidates.extend((full_end_seconds, active_end_seconds))

        start_seconds = min(start_candidates)
        end_seconds = max(end_candidates)
        if end_seconds <= start_seconds:
            end_seconds = start_seconds + 1.0

        padding_seconds = max(2.0, (end_seconds - start_seconds) * 0.05)
        return float(start_seconds - padding_seconds), float(end_seconds + padding_seconds)

    def refresh_timeline(self) -> None:
        if not hasattr(self, "timeline_canvas"):
            return
        if self._refreshing_timeline:
            return
        if not self.timeline_canvas.winfo_exists():
            return

        self._refreshing_timeline = True
        try:
            canvas = self.timeline_canvas
            canvas.delete("all")
            self.timeline_clip_item_by_session_id.clear()
            self.timeline_hit_regions.clear()

            render_sessions = self.get_render_sessions()
            if not render_sessions:
                canvas.configure(scrollregion=(0, 0, max(canvas.winfo_width(), 640), 220))
                canvas.create_text(
                    20,
                    110,
                    anchor="w",
                    fill="#5f6b7a",
                    text="添加 CSV 后，这里会显示统一时间轴。",
                )
                self.timeline_status_var.set("时间轴：默认自适应窗口；滚轮缩放，拖动片段对齐或裁剪。")
                return

            metrics = self._get_timeline_metrics()
            self.timeline_start_seconds, self.timeline_end_seconds = self.get_timeline_bounds()
            self.timeline_pixels_per_second = self._resolve_timeline_pixels_per_second(metrics)
            content_width = max(
                canvas.winfo_width(),
                int(
                    metrics["left_gutter"]
                    + metrics["right_padding"]
                    + max(self.timeline_end_seconds - self.timeline_start_seconds, 1.0) * self.timeline_pixels_per_second
                ),
            )
            content_height = int(
                metrics["tracks_top"]
                + len(render_sessions) * metrics["track_row_height"]
                + metrics["bottom_padding"]
            )
            canvas.configure(scrollregion=(0, 0, content_width, content_height))

            self._draw_timeline_axis(canvas, content_width, metrics)
            self._draw_timeline_sessions(canvas, render_sessions, metrics)

            selected_count = len(self.get_selected_session_ids())
            self.timeline_status_var.set(
                f"时间轴：{len(render_sessions)} 个文件，当前选中 {selected_count} 个；默认自适应窗口，滚轮可缩放细调。"
            )
        finally:
            self._refreshing_timeline = False

    @staticmethod
    def _get_timeline_metrics() -> dict[str, float]:
        return {
            "left_gutter": 150.0,
            "right_padding": 48.0,
            "top_padding": 16.0,
            "axis_y": 28.0,
            "tracks_top": 54.0,
            "track_row_height": 42.0,
            "clip_height": 22.0,
            "handle_width": 6.0,
            "bottom_padding": 28.0,
        }

    def _resolve_timeline_pixels_per_second(self, metrics: dict[str, float]) -> float:
        span_seconds = max(self.timeline_end_seconds - self.timeline_start_seconds, 1.0)
        viewport_width = max(float(self.timeline_canvas.winfo_width()), 640.0)
        usable_width = max(120.0, viewport_width - metrics["left_gutter"] - metrics["right_padding"])
        fitted_pixels_per_second = usable_width / span_seconds
        return max(0.1, fitted_pixels_per_second * self.timeline_zoom_factor)

    def _draw_timeline_axis(self, canvas: tk.Canvas, content_width: int, metrics: dict[str, float]) -> None:
        axis_y = metrics["axis_y"]
        left_x = metrics["left_gutter"]
        right_x = content_width - metrics["right_padding"]
        canvas.create_line(left_x, axis_y, right_x, axis_y, fill="#bac3cf")

        span_seconds = max(self.timeline_end_seconds - self.timeline_start_seconds, 1.0)
        candidate_steps = (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600)
        target_step_seconds = span_seconds / 8.0
        tick_step_seconds = candidate_steps[-1]
        for candidate_step_seconds in candidate_steps:
            if candidate_step_seconds >= target_step_seconds:
                tick_step_seconds = candidate_step_seconds
                break

        first_tick_seconds = (
            int(self.timeline_start_seconds / tick_step_seconds) * tick_step_seconds
        )
        if first_tick_seconds > self.timeline_start_seconds:
            first_tick_seconds -= tick_step_seconds

        tick_seconds = first_tick_seconds
        while tick_seconds <= self.timeline_end_seconds + tick_step_seconds:
            tick_x = self._timeline_seconds_to_x(tick_seconds, metrics)
            if left_x <= tick_x <= right_x:
                canvas.create_line(tick_x, axis_y - 5, tick_x, axis_y + 5, fill="#9aa5b5")
                canvas.create_text(
                    tick_x,
                    axis_y - 10,
                    text=format_compact_elapsed_time(tick_seconds),
                    fill="#516073",
                    font=("Segoe UI", 9),
                )
            tick_seconds += tick_step_seconds

    def _draw_timeline_sessions(
        self,
        canvas: tk.Canvas,
        render_sessions: Sequence[LoadedCsvSession],
        metrics: dict[str, float],
    ) -> None:
        selected_session_ids = set(self.get_selected_session_ids())
        for row_index, session in enumerate(render_sessions):
            row_top = metrics["tracks_top"] + row_index * metrics["track_row_height"]
            clip_top = row_top + 9
            clip_bottom = clip_top + metrics["clip_height"]
            full_start_seconds, full_end_seconds = compute_session_timeline_range(session)
            active_start_seconds, active_end_seconds = compute_session_active_timeline_range(session)
            full_left_x = self._timeline_seconds_to_x(full_start_seconds, metrics)
            full_right_x = self._timeline_seconds_to_x(full_end_seconds, metrics)
            active_left_x = self._timeline_seconds_to_x(active_start_seconds, metrics)
            active_right_x = self._timeline_seconds_to_x(active_end_seconds, metrics)
            handle_width = metrics["handle_width"]
            is_selected = session.session_id in selected_session_ids
            clip_fill = "#7ea7ff" if not session.is_reference else "#78b56d"
            clip_outline = "#1f4aa8" if is_selected else "#5578be"

            canvas.create_text(
                12,
                clip_top + metrics["clip_height"] / 2,
                anchor="w",
                fill="#2e3c4f",
                text=session.alias,
                font=("Segoe UI", 9, "bold" if is_selected else "normal"),
            )
            canvas.create_text(
                12,
                clip_top + metrics["clip_height"] / 2 + 13,
                anchor="w",
                fill="#748195",
                text=f"偏移 {self.format_offset_seconds(session.offset_seconds)}s",
                font=("Segoe UI", 8),
            )

            canvas.create_rectangle(
                full_left_x,
                clip_top,
                full_right_x,
                clip_bottom,
                fill="#e6ebf2",
                outline="#d1d9e5",
            )
            body_id = canvas.create_rectangle(
                active_left_x,
                clip_top,
                max(active_right_x, active_left_x + 1),
                clip_bottom,
                fill=clip_fill,
                outline=clip_outline,
                width=2 if is_selected else 1,
            )
            left_handle_id = canvas.create_rectangle(
                active_left_x - handle_width,
                clip_top - 2,
                active_left_x + handle_width,
                clip_bottom + 2,
                fill=clip_outline,
                outline="",
            )
            right_handle_id = canvas.create_rectangle(
                active_right_x - handle_width,
                clip_top - 2,
                active_right_x + handle_width,
                clip_bottom + 2,
                fill=clip_outline,
                outline="",
            )
            label_text = session.data.source_path.name
            if session.is_reference:
                label_text = f"{label_text}  [基准]"
            canvas.create_text(
                active_left_x + 8,
                clip_top + metrics["clip_height"] / 2,
                anchor="w",
                fill="#ffffff",
                text=label_text,
                font=("Segoe UI", 8, "bold"),
            )

            self.timeline_clip_item_by_session_id[session.session_id] = body_id
            self.timeline_hit_regions[body_id] = ("move_clip", session.session_id)
            self.timeline_hit_regions[left_handle_id] = ("trim_left", session.session_id)
            self.timeline_hit_regions[right_handle_id] = ("trim_right", session.session_id)

    def _timeline_seconds_to_x(self, seconds: float, metrics: dict[str, float] | None = None) -> float:
        resolved_metrics = metrics or self._get_timeline_metrics()
        return resolved_metrics["left_gutter"] + (
            float(seconds) - self.timeline_start_seconds
        ) * self.timeline_pixels_per_second

    def _timeline_x_to_seconds(self, x: float, metrics: dict[str, float] | None = None) -> float:
        resolved_metrics = metrics or self._get_timeline_metrics()
        return self.timeline_start_seconds + (
            float(x) - resolved_metrics["left_gutter"]
        ) / self.timeline_pixels_per_second

    def _find_timeline_hit(self, canvas_x: float, canvas_y: float) -> tuple[str, str | None] | None:
        if not hasattr(self, "timeline_canvas"):
            return None

        overlapping_item_ids = self.timeline_canvas.find_overlapping(
            canvas_x - 4,
            canvas_y - 4,
            canvas_x + 4,
            canvas_y + 4,
        )
        for item_id in reversed(overlapping_item_ids):
            hit = self.timeline_hit_regions.get(item_id)
            if hit is not None:
                return hit
        return None

    def _on_timeline_button_press(self, event) -> str | None:
        hit = self._find_timeline_hit(
            self.timeline_canvas.canvasx(event.x),
            self.timeline_canvas.canvasy(event.y),
        )
        if hit is None:
            self.timeline_drag_state = None
            return None

        action, session_id = hit
        if session_id is None:
            self.timeline_drag_state = None
            return None

        selected_session_ids = set(self.get_selected_session_ids())
        if session_id not in selected_session_ids:
            self.session_tree.selection_set((session_id,))
            self.session_tree.focus(session_id)
            selected_session_ids = {session_id}
            self.sync_session_editor()
            self.refresh_timeline()

        if action == "move_clip":
            tracked_session_ids = tuple(selected_session_ids)
        else:
            tracked_session_ids = (session_id,)
        start_offsets = {
            tracked_session_id: float(self.get_session_by_id(tracked_session_id).offset_seconds)
            for tracked_session_id in tracked_session_ids
            if self.get_session_by_id(tracked_session_id) is not None
        }
        start_trim_ranges = {
            tracked_session_id: resolve_session_source_trim_range(self.get_session_by_id(tracked_session_id))
            for tracked_session_id in tracked_session_ids
            if self.get_session_by_id(tracked_session_id) is not None
        }

        self.timeline_drag_state = {
            "action": action,
            "start_x": self.timeline_canvas.canvasx(event.x),
            "session_ids": tracked_session_ids,
            "start_offsets": start_offsets,
            "start_trim_ranges": start_trim_ranges,
        }
        return "break"

    def _on_timeline_drag(self, event) -> str | None:
        if self.timeline_drag_state is None:
            return None

        action = str(self.timeline_drag_state["action"])
        canvas_x = self.timeline_canvas.canvasx(event.x)
        delta_seconds = self._snap_timeline_delta_seconds(
            (canvas_x - float(self.timeline_drag_state["start_x"])) / self.timeline_pixels_per_second,
            event,
        )

        if action == "move_clip":
            updated_session_by_id: dict[str, LoadedCsvSession] = {}
            for session_id in self.timeline_drag_state["session_ids"]:
                session = self.get_session_by_id(session_id)
                if session is None:
                    continue
                start_offset_seconds = self.timeline_drag_state["start_offsets"].get(session_id, session.offset_seconds)
                updated_session_by_id[session_id] = replace(
                    session,
                    offset_seconds=float(start_offset_seconds) + delta_seconds,
                )
            if updated_session_by_id:
                self._apply_timeline_session_updates(updated_session_by_id)
                self._schedule_timeline_preview_refresh()
            return "break"

        if action in {"trim_left", "trim_right"}:
            session_id = next(iter(self.timeline_drag_state["session_ids"]), None)
            session = self.get_session_by_id(session_id) if session_id is not None else None
            if session is None:
                return "break"

            full_start_seconds = float(session.data.elapsed_seconds[0]) if session.data.elapsed_seconds else 0.0
            full_end_seconds = float(session.data.elapsed_seconds[-1]) if session.data.elapsed_seconds else 0.0
            start_trim_start_seconds, start_trim_end_seconds = self.timeline_drag_state["start_trim_ranges"].get(
                session_id,
                resolve_session_source_trim_range(session),
            )
            if action == "trim_left":
                resolved_trim_start_seconds = self._snap_timeline_value_seconds(
                    min(start_trim_end_seconds, max(full_start_seconds, start_trim_start_seconds + delta_seconds)),
                    event,
                )
                updated_session = replace(
                    session,
                    source_trim_start_seconds=resolved_trim_start_seconds,
                )
            else:
                resolved_trim_end_seconds = self._snap_timeline_value_seconds(
                    max(start_trim_start_seconds, min(full_end_seconds, start_trim_end_seconds + delta_seconds)),
                    event,
                )
                updated_session = replace(
                    session,
                    source_trim_end_seconds=None if abs(resolved_trim_end_seconds - full_end_seconds) < 1e-9 else resolved_trim_end_seconds,
                )

            self._apply_timeline_session_updates({session.session_id: updated_session})
            self._schedule_timeline_preview_refresh()
            return "break"

        return "break"

    def _on_timeline_button_release(self, _event=None) -> str | None:
        if self.timeline_drag_state is None:
            return None

        self.timeline_drag_state = None
        self._schedule_timeline_preview_refresh(immediate=True)
        return "break"

    @staticmethod
    def _normalize_signed_zero(value: float) -> float:
        if abs(value) < 1e-9:
            return 0.0
        return float(value)

    def _snap_timeline_value_seconds(self, value: float, event) -> float:
        step_seconds = 0.1 if bool(getattr(event, "state", 0) & 0x0001) else 1.0
        snapped_value = round(float(value) / step_seconds) * step_seconds
        return self._normalize_signed_zero(snapped_value)

    def _snap_timeline_delta_seconds(self, delta_seconds: float, event) -> float:
        return self._snap_timeline_value_seconds(delta_seconds, event)

    def _apply_timeline_session_updates(self, updated_session_by_id: dict[str, LoadedCsvSession]) -> None:
        preferred_selection = list(self.get_selected_session_ids()) or list(updated_session_by_id)
        self.sessions = [
            updated_session_by_id.get(session.session_id, session)
            for session in self.sessions
        ]
        self.refresh_session_tree(preferred_selection=preferred_selection)
        self.refresh_extrema_source_options()
        self.refresh_extrema_groups()
        self.configure_trim_controls(preserve_range=True)

    def _schedule_timeline_preview_refresh(self, *, immediate: bool = False) -> None:
        if self.timeline_preview_after_id is not None:
            try:
                self.after_cancel(self.timeline_preview_after_id)
            except tk.TclError:
                pass
            self.timeline_preview_after_id = None

        if immediate:
            self.schedule_preview_refresh(immediate=True)
            return

        self.timeline_preview_after_id = self.after(140, self._run_timeline_preview_refresh)

    def _run_timeline_preview_refresh(self) -> None:
        self.timeline_preview_after_id = None
        self.schedule_preview_refresh(immediate=True)

    def reset_selected_session_offsets(self) -> None:
        target_session_ids = self.get_selected_session_ids() or tuple(session.session_id for session in self.get_render_sessions())
        updated_session_by_id = {}
        for session_id in target_session_ids:
            session = self.get_session_by_id(session_id)
            if session is None:
                continue
            updated_session_by_id[session_id] = replace(session, offset_seconds=0.0)
        if not updated_session_by_id:
            return

        self._apply_timeline_session_updates(updated_session_by_id)
        self.schedule_preview_refresh(immediate=True)
        self.status_var.set("已重置所选文件的偏移。")

    def reset_selected_session_source_trims(self) -> None:
        target_session_ids = self.get_selected_session_ids() or tuple(session.session_id for session in self.get_render_sessions())
        updated_session_by_id = {}
        for session_id in target_session_ids:
            session = self.get_session_by_id(session_id)
            if session is None:
                continue
            updated_session_by_id[session_id] = replace(
                session,
                source_trim_start_seconds=0.0,
                source_trim_end_seconds=None,
            )
        if not updated_session_by_id:
            return

        self._apply_timeline_session_updates(updated_session_by_id)
        self.schedule_preview_refresh(immediate=True)
        self.status_var.set("已重置所选文件的有效片段。")

    def browse_csv(self) -> None:
        file_paths = filedialog.askopenfilenames(
            title="选择 HWiNFO CSV 文件",
            filetypes=[("CSV 文件", "*.csv;*.CSV"), ("所有文件", "*.*")],
        )
        if not file_paths:
            return

        self.add_csv_files(file_paths)

    def _build_chart_color_row(
        self,
        parent,
        *,
        row: int,
        label_text: str,
        color_var: tk.StringVar,
        field_name: str,
    ) -> None:
        ttk.Label(parent, text=label_text).grid(row=row, column=0, sticky="w", pady=(8, 0), padx=(0, 8))

        entry = ttk.Entry(parent, textvariable=color_var, width=10)
        entry.grid(row=row, column=1, sticky="ew", pady=(8, 0))

        button_row = ttk.Frame(parent)
        button_row.grid(row=row, column=2, columnspan=2, sticky="ew", pady=(8, 0))
        button_row.columnconfigure((0, 1), weight=1)

        ttk.Button(
            button_row,
            text="取色器...",
            command=lambda selected_var=color_var, selected_field_name=field_name: self.choose_chart_option_color(
                selected_var,
                selected_field_name,
            ),
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(
            button_row,
            text="清除",
            command=lambda selected_var=color_var: self.clear_chart_option_color(selected_var),
        ).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        entry.bind(
            "<Return>",
            lambda _event, selected_var=color_var: self.apply_chart_option_color_entry(selected_var),
        )

    def apply_chart_option_color_entry(self, color_var: tk.StringVar) -> str:
        color_text = color_var.get().strip()
        if color_text:
            try:
                self._set_chart_option_var(color_var, self.normalize_hex_color(color_text).removeprefix("#").upper())
            except ValueError:
                pass

        self.schedule_preview_refresh(immediate=True)
        return "break"

    def choose_chart_option_color(self, color_var: tk.StringVar, field_name: str) -> None:
        initial_color = None
        try:
            initial_color = self.normalize_hex_color(color_var.get())
        except ValueError:
            pass

        _, selected_color = colorchooser.askcolor(
            title=f"选择{field_name}",
            initialcolor=initial_color,
        )
        if not selected_color:
            return

        self._set_chart_option_var(color_var, selected_color.removeprefix("#").upper())
        self.schedule_preview_refresh(immediate=True)

    def clear_chart_option_color(self, color_var: tk.StringVar) -> None:
        if not color_var.get().strip():
            return

        self._set_chart_option_var(color_var, "")
        self.schedule_preview_refresh(immediate=True)

    def _set_chart_option_var(self, color_var: tk.StringVar, value: str) -> None:
        self._suppress_chart_option_refresh = True
        try:
            color_var.set(value)
        finally:
            self._suppress_chart_option_refresh = False

    def enable_file_drop(self) -> None:
        file_drop_manager = WindowsFileDropManager(self)
        if file_drop_manager.register():
            self.file_drop_manager = file_drop_manager
            self.schedule_file_drop_processing()
            logger.info("Enabled native Windows file drop support")
            return

        logger.debug("Native Windows file drop support unavailable")

    def schedule_file_drop_processing(self) -> None:
        if self.file_drop_after_id is not None:
            return

        self.file_drop_after_id = self.after(100, self.process_dropped_files)

    def process_dropped_files(self) -> None:
        self.file_drop_after_id = None
        if self.file_drop_manager is None:
            return

        while True:
            file_paths = self.file_drop_manager.pop_dropped_paths()
            if file_paths is None:
                break
            self.handle_dropped_files(file_paths)

        if not self.preview_shutdown_event.is_set() and self.file_drop_manager is not None:
            self.schedule_file_drop_processing()

    def handle_dropped_files(self, file_paths: Sequence[str]) -> None:
        csv_paths = self.pick_csv_drop_paths(file_paths)
        logger.info(
            "Handling dropped files received=%d accepted_csv=%d",
            len(file_paths),
            len(csv_paths),
        )
        if not csv_paths:
            messagebox.showerror("未找到 CSV", "请拖入 .csv 或 .CSV 文件。")
            return

        self.add_csv_files(csv_paths)

    @staticmethod
    def pick_csv_drop_paths(file_paths: Sequence[str]) -> tuple[str, ...]:
        csv_paths: list[str] = []
        seen_paths: set[str] = set()
        for file_path in file_paths:
            if Path(file_path).suffix.lower() == ".csv":
                normalized_path = HWiNFOPlotterApp.normalize_session_path(file_path)
                if normalized_path in seen_paths:
                    continue
                seen_paths.add(normalized_path)
                csv_paths.append(file_path)
        return tuple(csv_paths)

    def load_current_file(self) -> None:
        messagebox.showinfo("已改为多文件模式", "请使用“添加 CSV...”按钮或直接拖入一个或多个 CSV 文件。")

    def refresh_column_list(self) -> None:
        self.column_listbox.delete(0, tk.END)
        self.visible_parameter_columns.clear()

        if not self.sessions:
            self.selection_var.set("当前未选择参数")
            return

        keyword = self.filter_var.get().strip().lower()
        selected_parameter_keys = set(self.get_selected_parameter_shared_keys())
        for column in self.get_shared_parameter_columns():
            haystack = f"{column.name} {column.display_name}".lower()
            if keyword and keyword not in haystack:
                continue

            self.visible_parameter_columns.append(column)
            self.column_listbox.insert(tk.END, column.display_name)

        for listbox_index, column in enumerate(self.visible_parameter_columns):
            if column.shared_key in selected_parameter_keys:
                self.column_listbox.selection_set(listbox_index)

        self.update_selection_label()

    def on_column_selection_changed(self, _event=None) -> None:
        visible_parameter_keys = {column.shared_key for column in self.visible_parameter_columns}
        selected_parameter_keys = set(self.get_selected_parameter_shared_keys())
        selected_parameter_keys -= visible_parameter_keys

        for selected_position in self.column_listbox.curselection():
            if selected_position >= len(self.visible_parameter_columns):
                continue
            selected_parameter_keys.add(self.visible_parameter_columns[selected_position].shared_key)

        self.selected_series_keys = self.expand_series_keys_for_parameter_shared_keys(selected_parameter_keys)

        self.update_selection_label()
        self.refresh_selected_series_list()
        self.schedule_preview_refresh()

    def update_selection_label(self) -> None:
        count = len(self.get_selected_parameter_shared_keys())
        if count == 0:
            self.selection_var.set("当前未选择参数")
        else:
            self.selection_var.set(f"当前已选择 {count} 个参数")

    def refresh_selected_series_list(self) -> None:
        self.selected_series_listbox.delete(0, tk.END)

        if not self.sessions:
            return

        selected_descriptors = self.get_selected_series_descriptors()
        for listbox_index, descriptor in enumerate(selected_descriptors):
            color_text = self.series_colors.get(descriptor.key)
            base_text = self.format_series_list_label(descriptor)
            display_text = base_text if not color_text else f"{base_text}  ·  {color_text.upper()}"
            self.selected_series_listbox.insert(tk.END, display_text)
            if color_text:
                self.selected_series_listbox.itemconfig(listbox_index, foreground=color_text)

    def _on_series_color_submitted(self, _event=None) -> str:
        self.apply_series_color()
        return "break"

    def apply_series_color(self) -> None:
        if not self.sessions:
            return

        selected_positions = self.selected_series_listbox.curselection()
        if not selected_positions:
            messagebox.showinfo("未选择参数", "请先在“参数颜色”列表中选择一个或多个参数。")
            return

        try:
            selected_color = self.normalize_hex_color(self.series_color_var.get())
        except ValueError as exc:
            messagebox.showerror("颜色格式无效", str(exc))
            return

        self.series_color_var.set(selected_color.removeprefix("#").upper())
        selected_descriptors = self.get_selected_series_descriptors()
        for position in selected_positions:
            if position >= len(selected_descriptors):
                continue
            self.series_colors[selected_descriptors[position].key] = selected_color

        self.refresh_selected_series_list()
        self.refresh_column_list()
        self.schedule_preview_refresh(immediate=True)

    def choose_series_color(self) -> None:
        if not self.sessions:
            return

        selected_positions = self.selected_series_listbox.curselection()
        if not selected_positions:
            messagebox.showinfo("未选择参数", "请先在“参数颜色”列表中选择一个或多个参数。")
            return

        initial_color = None
        try:
            initial_color = self.normalize_hex_color(self.series_color_var.get())
        except ValueError:
            pass

        _, selected_color = colorchooser.askcolor(
            title="选择参数颜色",
            initialcolor=initial_color,
        )
        if not selected_color:
            return

        self.series_color_var.set(selected_color.removeprefix("#").upper())
        self.apply_series_color()

    def clear_series_color(self) -> None:
        if not self.sessions:
            return

        selected_positions = self.selected_series_listbox.curselection()
        if not selected_positions:
            messagebox.showinfo("未选择参数", "请先在“参数颜色”列表中选择一个或多个参数。")
            return

        selected_descriptors = self.get_selected_series_descriptors()
        changed = False
        for position in selected_positions:
            if position >= len(selected_descriptors):
                continue
            changed = self.series_colors.pop(selected_descriptors[position].key, None) is not None or changed

        if changed:
            self.refresh_selected_series_list()
            self.refresh_column_list()
            self.schedule_preview_refresh(immediate=True)

    def select_all_visible(self) -> None:
        if not self.visible_parameter_columns:
            return

        self.column_listbox.selection_set(0, tk.END)
        selected_parameter_keys = set(self.get_selected_parameter_shared_keys())
        selected_parameter_keys.update(column.shared_key for column in self.visible_parameter_columns)
        self.selected_series_keys = self.expand_series_keys_for_parameter_shared_keys(selected_parameter_keys)
        self.update_selection_label()
        self.refresh_selected_series_list()
        self.schedule_preview_refresh()

    def clear_selection(self) -> None:
        self.selected_series_keys.clear()
        self.column_listbox.selection_clear(0, tk.END)
        self.update_selection_label()
        self.refresh_selected_series_list()
        self.cancel_pending_preview_requests()
        self.clear_preview()
        self.status_var.set("已清空选择。")
        self.schedule_preview_refresh(immediate=True)

    def reset_chart_options(self) -> None:
        self.title_var.set("")
        self.width_var.set("1920")
        self.height_var.set("1080")
        self.dpi_var.set("160")
        self.line_width_var.set("1.8")
        self.time_density_var.set(DEFAULT_TIME_TICK_DENSITY)
        self.fixed_time_interval_var.set("")
        self.fixed_time_interval_unit_var.set("自动")
        self.curve_only_mode_var.set(False)
        self.show_grid_var.set(True)
        self.show_legend_var.set(True)
        self.show_time_axis_var.set(True)
        self.show_value_axis_var.set(True)
        self.legend_location_var.set("自动")
        self.axis_color_var.set("")
        self.grid_color_var.set("")
        self.time_text_color_var.set("")
        self.value_text_color_var.set("")
        self.legend_text_color_var.set("")
        self.font_family_var.set("自动")
        self.status_var.set("图表样式已重置。")
        self.schedule_preview_refresh()

    def schedule_preview_refresh(self, *, immediate: bool = False) -> None:
        if self.preview_after_id is not None:
            try:
                self.after_cancel(self.preview_after_id)
            except tk.TclError:
                pass
            self.preview_after_id = None

        delay_ms = 0 if immediate else 350
        self.preview_after_id = self.after(delay_ms, self.refresh_preview)
        logger.debug("Scheduled preview refresh immediate=%s delay_ms=%d", immediate, delay_ms)

    def refresh_preview(self) -> None:
        self.preview_after_id = None
        if not self.sessions:
            self.cancel_pending_preview_requests()
            self.clear_preview()
            return

        if not self.get_selected_series_keys():
            self.cancel_pending_preview_requests()
            self.clear_preview()
            return

        try:
            preview_request = self.build_preview_request()
        except Exception as exc:
            logger.exception("Failed to build preview request")
            self.status_var.set(f"自动预览未更新：{exc}")
            return

        self.enqueue_preview_request(preview_request)
        logger.info(
            "Queued preview request request_id=%d sessions=%d selected_series=%d",
            preview_request.request_id,
            len(preview_request.sessions),
            len(preview_request.selected_series),
        )
        self.status_var.set("正在后台生成图表预览...")

    def export_png(self) -> None:
        if not self.sessions:
            messagebox.showerror("尚未加载", "请先加载一个或多个 CSV 文件。")
            return

        selected_series = self.get_selected_series_keys()
        if not selected_series:
            messagebox.showerror("未选择参数", "请至少选择一个参数。")
            return

        render_sessions = self.get_render_sessions()
        if len(render_sessions) == 1 and all(series_key.session_id == render_sessions[0].session_id for series_key in selected_series):
            default_name = build_default_output_name(
                render_sessions[0].data,
                [series_key.column_index for series_key in selected_series],
            )
        else:
            default_name = build_comparison_output_name(render_sessions, selected_series)
        logger.info(
            "Preparing PNG export sessions=%d selected_series=%d suggested_name=%s",
            len(render_sessions),
            len(selected_series),
            default_name,
        )
        output_path = filedialog.asksaveasfilename(
            title="导出透明 PNG",
            defaultextension=".png",
            initialfile=default_name,
            filetypes=[("PNG 文件", "*.png")],
        )
        if not output_path:
            return

        figure = None
        try:
            figure = self.build_current_figure()
            destination = save_figure(figure, output_path)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        finally:
            if figure is not None:
                figure.clear()

        self.status_var.set(f"已导出透明 PNG：{destination}")

    def build_current_figure(self):
        (
            selected_series,
            width_px,
            height_px,
            dpi,
            style,
            color_by_series,
            visible_range_seconds,
        ) = self.collect_render_options()
        render_sessions = self.get_render_sessions()
        if not render_sessions:
            raise ValueError("请先加载一个或多个 CSV 文件。")
        extrema_config = self.build_extrema_detection_config()
        logger.info(
            (
                "Building current figure sessions=%d selected_series=%d size=%dx%d dpi=%d "
                "visible_range=%s selected_detail=%s"
            ),
            len(render_sessions),
            len(selected_series),
            width_px,
            height_px,
            dpi,
            visible_range_seconds if visible_range_seconds is not None else "auto",
            self._summarize_series_keys_for_log(selected_series),
        )

        return build_comparison_figure(
            render_sessions,
            selected_series,
            width_px=width_px,
            height_px=height_px,
            dpi=dpi,
            style=style,
            color_by_series=color_by_series,
            visible_range_seconds=visible_range_seconds,
            extrema_config=extrema_config,
            extrema_assignments=dict(self.extrema_assignments),
            extrema_point_colors=dict(self.extrema_point_colors),
        )

    def build_preview_request(self) -> PreviewRenderRequest:
        (
            selected_series,
            width_px,
            height_px,
            dpi,
            style,
            color_by_series,
            visible_range_seconds,
        ) = self.collect_render_options()
        render_sessions = self.get_render_sessions()
        if not render_sessions:
            raise ValueError("请先加载一个或多个 CSV 文件。")
        extrema_config = self.build_extrema_detection_config()

        self.preview_request_id += 1
        self.active_preview_request_id = self.preview_request_id
        preview_request = PreviewRenderRequest(
            request_id=self.preview_request_id,
            sessions=render_sessions,
            selected_series=tuple(selected_series),
            width_px=width_px,
            height_px=height_px,
            dpi=dpi,
            style=style,
            color_by_series=color_by_series,
            visible_range_seconds=visible_range_seconds,
            extrema_config=extrema_config,
            extrema_assignments=dict(self.extrema_assignments),
            extrema_point_colors=dict(self.extrema_point_colors),
        )
        logger.debug(
            (
                "Built preview request request_id=%d sessions=%d selected_series=%d "
                "size=%dx%d dpi=%d visible_range=%s"
            ),
            preview_request.request_id,
            len(preview_request.sessions),
            len(preview_request.selected_series),
            preview_request.width_px,
            preview_request.height_px,
            preview_request.dpi,
            preview_request.visible_range_seconds if preview_request.visible_range_seconds is not None else "auto",
        )
        return preview_request

    def collect_render_options(
        self,
    ) -> tuple[list[SeriesKey], int, int, int, ChartStyle, dict[SeriesKey, str], tuple[float, float] | None]:
        selected_series = self.get_selected_series_keys()
        if not selected_series:
            raise ValueError("请至少选择一个参数。")

        width_px = self.parse_positive_int(self.width_var.get(), "宽度")
        height_px = self.parse_positive_int(self.height_var.get(), "高度")
        dpi = self.parse_positive_int(self.dpi_var.get(), "DPI")
        line_width = self.parse_positive_float(self.line_width_var.get(), "曲线线宽")
        style = ChartStyle(
            title=self.title_var.get().strip() or None,
            font_family=self.parse_optional_font_family(self.font_family_var.get()),
            line_width=line_width,
            curve_only_mode=self.curve_only_mode_var.get(),
            show_grid=self.show_grid_var.get(),
            show_legend=self.show_legend_var.get(),
            show_time_axis=self.show_time_axis_var.get(),
            show_value_axis=self.show_value_axis_var.get(),
            axis_color=self.parse_optional_hex_color(self.axis_color_var.get(), "坐标轴颜色"),
            grid_color=self.parse_optional_hex_color(self.grid_color_var.get(), "网格颜色"),
            time_text_color=self.parse_optional_hex_color(self.time_text_color_var.get(), "时间文字颜色"),
            value_text_color=self.parse_optional_hex_color(self.value_text_color_var.get(), "数值文字颜色"),
            legend_text_color=self.parse_optional_hex_color(self.legend_text_color_var.get(), "图例文字颜色"),
            legend_location=LEGEND_LOCATION_CHOICES.get(self.legend_location_var.get(), "best"),
            time_tick_density=self.parse_time_tick_density(),
            fixed_time_interval_seconds=self.parse_fixed_time_interval_seconds(),
        )
        color_by_series = {
            series_key: color_text
            for series_key, color_text in self.series_colors.items()
            if series_key in self.selected_series_keys
        }
        visible_range_seconds = self.get_visible_range_seconds()

        return selected_series, width_px, height_px, dpi, style, color_by_series, visible_range_seconds

    def enqueue_preview_request(self, preview_request: PreviewRenderRequest) -> None:
        with self.preview_request_lock:
            self.pending_preview_request = preview_request
            self.preview_request_event.set()
        logger.debug("Enqueued preview request request_id=%d", preview_request.request_id)

    def cancel_pending_preview_requests(self) -> None:
        self.preview_request_id += 1
        self.active_preview_request_id = self.preview_request_id
        with self.preview_request_lock:
            self.pending_preview_request = None
            self.preview_request_event.clear()
        logger.debug("Cancelled pending preview requests active_request_id=%d", self.active_preview_request_id)

    def start_background_preload(self, session: LoadedCsvSession) -> None:
        self.preload_request_id += 1
        preload_request = PreloadSeriesRequest(
            request_id=self.preload_request_id,
            session_id=session.session_id,
            data=session.data,
        )
        self.preload_requests.put(preload_request)
        logger.info(
            "Queued background preload request_id=%d session_id=%s source=%s",
            preload_request.request_id,
            session.session_id,
            session.data.source_path,
        )

    def cancel_pending_preload_requests(self) -> None:
        while True:
            try:
                self.preload_requests.get_nowait()
            except Empty:
                break

    def _preview_worker_loop(self) -> None:
        logger.debug("Preview worker loop started")
        while not self.preview_shutdown_event.is_set():
            self.preview_request_event.wait(0.1)
            if self.preview_shutdown_event.is_set():
                return
            if not self.preview_request_event.is_set():
                continue

            with self.preview_request_lock:
                preview_request = self.pending_preview_request
                self.pending_preview_request = None
                self.preview_request_event.clear()

            if preview_request is None:
                continue

            figure = None
            started_at = perf_counter()
            logger.info(
                "Rendering preview request_id=%d sessions=%d selected_series=%d",
                preview_request.request_id,
                len(preview_request.sessions),
                len(preview_request.selected_series),
            )
            try:
                figure = build_comparison_figure(
                    preview_request.sessions,
                    preview_request.selected_series,
                    width_px=preview_request.width_px,
                    height_px=preview_request.height_px,
                    dpi=preview_request.dpi,
                    style=preview_request.style,
                    color_by_series=preview_request.color_by_series,
                    visible_range_seconds=preview_request.visible_range_seconds,
                    extrema_config=preview_request.extrema_config,
                    extrema_assignments=preview_request.extrema_assignments,
                    extrema_point_colors=preview_request.extrema_point_colors,
                )
                png_bytes = render_figure_png_bytes(figure)
            except Exception as exc:
                self.preview_results.put(
                    PreviewRenderResult(
                        request_id=preview_request.request_id,
                        error_message=str(exc),
                    )
                )
                logger.exception("Preview render failed request_id=%d", preview_request.request_id)
                continue
            finally:
                if figure is not None:
                    figure.clear()

            self.preview_results.put(
                PreviewRenderResult(
                    request_id=preview_request.request_id,
                    png_bytes=png_bytes,
                )
            )
            logger.info(
                "Rendered preview request_id=%d png_bytes=%d elapsed_ms=%.2f",
                preview_request.request_id,
                len(png_bytes),
                (perf_counter() - started_at) * 1000,
            )

    def _preload_worker_loop(self) -> None:
        logger.debug("Preload worker loop started")
        while not self.preview_shutdown_event.is_set():
            try:
                preload_request = self.preload_requests.get(timeout=0.1)
            except Empty:
                continue
            if self.preview_shutdown_event.is_set():
                return

            try:
                started_at = perf_counter()
                logger.info(
                    "Preloading session data request_id=%d session_id=%s",
                    preload_request.request_id,
                    preload_request.session_id,
                )
                preload_request.data.preload_numeric_series()
            except Exception as exc:
                self.preload_results.put(
                    PreloadSeriesResult(
                        request_id=preload_request.request_id,
                        session_id=preload_request.session_id,
                        error_message=str(exc),
                    )
                )
                logger.exception(
                    "Background preload failed request_id=%d session_id=%s",
                    preload_request.request_id,
                    preload_request.session_id,
                )
                continue

            self.preload_results.put(
                PreloadSeriesResult(
                    request_id=preload_request.request_id,
                    session_id=preload_request.session_id,
                )
            )
            logger.info(
                "Preloaded session data request_id=%d session_id=%s elapsed_ms=%.2f",
                preload_request.request_id,
                preload_request.session_id,
                (perf_counter() - started_at) * 1000,
            )

    def process_preview_results(self) -> None:
        try:
            while True:
                preview_result = self.preview_results.get_nowait()
                if preview_result.request_id != self.active_preview_request_id:
                    logger.debug(
                        "Discarded stale preview result request_id=%d active_request_id=%d",
                        preview_result.request_id,
                        self.active_preview_request_id,
                    )
                    continue

                if preview_result.error_message is not None:
                    logger.warning(
                        "Preview result failed request_id=%d error=%s",
                        preview_result.request_id,
                        preview_result.error_message,
                    )
                    self.status_var.set(f"自动预览未更新：{preview_result.error_message}")
                    continue

                if preview_result.png_bytes is not None:
                    logger.info(
                        "Applying preview result request_id=%d png_bytes=%d",
                        preview_result.request_id,
                        len(preview_result.png_bytes),
                    )
                    self.show_preview_image(preview_result.png_bytes)
                    self.status_var.set("图表预览已在后台更新。")
        except Empty:
            pass

        try:
            while True:
                preload_result = self.preload_results.get_nowait()
                session = self.get_session_by_id(preload_result.session_id)
                if session is None:
                    continue

                self.sessions = [
                    replace(
                        current_session,
                        preload_ready=preload_result.error_message is None,
                        preload_error=preload_result.error_message,
                    )
                    if current_session.session_id == preload_result.session_id
                    else current_session
                    for current_session in self.sessions
                ]
                self.refresh_session_tree(preferred_selection=self.get_selected_session_ids())

                if preload_result.error_message is not None:
                    logger.warning(
                        "Preload result failed request_id=%d session_id=%s error=%s",
                        preload_result.request_id,
                        preload_result.session_id,
                        preload_result.error_message,
                    )
                    if not self.get_selected_series_keys():
                        self.status_var.set(f"后台预载失败：{preload_result.error_message}")
                    continue

                if not self.get_selected_series_keys():
                    logger.info(
                        "Preload result applied request_id=%d session_id=%s",
                        preload_result.request_id,
                        preload_result.session_id,
                    )
                    session_alias = session.alias if session.alias else session.data.source_path.stem
                    self.status_var.set(f"{session_alias} 的数值序列已在后台预载入内存。")
        except Empty:
            pass
        finally:
            if not self.preview_shutdown_event.is_set():
                self.process_results_after_id = self.after(80, self.process_preview_results)

    def on_close(self) -> None:
        logger.info(
            "Closing application total_sessions=%d session_detail=%s",
            len(self.sessions),
            self._summarize_sessions_for_log(),
        )
        self._on_about_window_closed()
        if self.file_drop_after_id is not None:
            try:
                self.after_cancel(self.file_drop_after_id)
            except tk.TclError:
                pass
            self.file_drop_after_id = None
        if self.process_results_after_id is not None:
            try:
                self.after_cancel(self.process_results_after_id)
            except tk.TclError:
                pass
            self.process_results_after_id = None
        if self.default_csv_after_id is not None:
            try:
                self.after_cancel(self.default_csv_after_id)
            except tk.TclError:
                pass
            self.default_csv_after_id = None
        if self.file_drop_manager is not None:
            self.file_drop_manager.unregister()
            self.file_drop_manager = None
        self.preview_shutdown_event.set()
        self.preview_request_event.set()
        if self.preview_after_id is not None:
            try:
                self.after_cancel(self.preview_after_id)
            except tk.TclError:
                pass
            self.preview_after_id = None
        if self.preview_display_after_id is not None:
            try:
                self.after_cancel(self.preview_display_after_id)
            except tk.TclError:
                pass
            self.preview_display_after_id = None
        if self.timeline_preview_after_id is not None:
            try:
                self.after_cancel(self.timeline_preview_after_id)
            except tk.TclError:
                pass
            self.timeline_preview_after_id = None
        logger.info("Destroying Tk application window")
        self.destroy()

    def get_selected_series_keys(self) -> list[SeriesKey]:
        if not self.sessions:
            return []

        selected_set = set(self.selected_series_keys)
        return [
            descriptor.key
            for descriptor in build_series_descriptors(self.get_render_sessions())
            if descriptor.key in selected_set
        ]

    def get_selected_series_descriptors(self) -> list[SeriesDescriptor]:
        if not self.sessions:
            return []

        selected_set = set(self.selected_series_keys)
        return [
            descriptor
            for descriptor in build_series_descriptors(self.get_render_sessions())
            if descriptor.key in selected_set
        ]

    def show_preview_image(self, png_bytes: bytes) -> None:
        self.clear_preview()

        self.preview_placeholder.grid_remove()
        self.preview_source_png_bytes = png_bytes
        self.preview_source_size = get_png_dimensions(png_bytes)
        self.preview_display_size = None
        self.preview_label = ttk.Label(
            self.preview_host,
            anchor="center",
        )
        self.preview_label.grid(row=0, column=0, sticky="nsew")
        self.refresh_preview_display()

    def schedule_preview_display_refresh(self, *, immediate: bool = False) -> None:
        if self.preview_display_after_id is not None:
            try:
                self.after_cancel(self.preview_display_after_id)
            except tk.TclError:
                pass
            self.preview_display_after_id = None

        delay_ms = 0 if immediate else 80
        self.preview_display_after_id = self.after(delay_ms, self.refresh_preview_display)

    def refresh_preview_display(self) -> None:
        self.preview_display_after_id = None
        if (
            self.preview_source_png_bytes is None
            or self.preview_source_size is None
            or self.preview_label is None
        ):
            return

        available_width = self.preview_host.winfo_width()
        available_height = self.preview_host.winfo_height()
        if available_width <= 1 or available_height <= 1:
            self.schedule_preview_display_refresh()
            return

        target_size = fit_size_within_bounds(
            self.preview_source_size[0],
            self.preview_source_size[1],
            available_width,
            available_height,
        )
        if target_size == self.preview_display_size and self.preview_image is not None:
            return

        display_png_bytes = self.preview_source_png_bytes
        if target_size != self.preview_source_size:
            try:
                display_png_bytes = resize_png_bytes(
                    self.preview_source_png_bytes,
                    target_size[0],
                    target_size[1],
                )
            except Exception as exc:
                self.status_var.set(f"图表预览缩放未更新：{exc}")
                self.preview_display_size = None
                return

        self.preview_image = self.create_photo_image(display_png_bytes)
        self.preview_label.configure(image=self.preview_image)
        self.preview_display_size = target_size

    @staticmethod
    def create_photo_image(png_bytes: bytes) -> tk.PhotoImage:
        return tk.PhotoImage(
            data=base64.b64encode(png_bytes).decode("ascii"),
            format="png",
        )

    def clear_preview(self) -> None:
        if self.preview_display_after_id is not None:
            try:
                self.after_cancel(self.preview_display_after_id)
            except tk.TclError:
                pass
            self.preview_display_after_id = None
        if self.preview_label is not None:
            self.preview_label.destroy()
            self.preview_label = None

        self.preview_image = None
        self.preview_source_png_bytes = None
        self.preview_source_size = None
        self.preview_display_size = None
        self.preview_placeholder.grid(row=0, column=0, sticky="nsew")

    @staticmethod
    def parse_positive_int(value: str, field_name: str) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError(f"{field_name} 必须是整数。") from exc

        if parsed <= 0:
            raise ValueError(f"{field_name} 必须大于 0。")

        return parsed

    @staticmethod
    def parse_positive_float(value: str, field_name: str) -> float:
        try:
            parsed = float(value.strip().replace(",", "."))
        except ValueError as exc:
            raise ValueError(f"{field_name} 必须是数字。") from exc

        if parsed <= 0:
            raise ValueError(f"{field_name} 必须大于 0。")

        return parsed

    @staticmethod
    def parse_float(value: str, field_name: str) -> float:
        cleaned_value = value.strip().replace(",", ".")
        if not cleaned_value:
            return 0.0

        try:
            return float(cleaned_value)
        except ValueError as exc:
            raise ValueError(f"{field_name} 必须是数字。") from exc

    @classmethod
    def parse_nonnegative_float(cls, value: str, field_name: str) -> float:
        parsed = cls.parse_float(value, field_name)
        if parsed < 0:
            raise ValueError(f"{field_name} 不能小于 0。")
        return parsed

    @staticmethod
    def parse_min_int(value: str, field_name: str, *, minimum: int) -> int:
        try:
            parsed = int(value.strip())
        except ValueError as exc:
            raise ValueError(f"{field_name} 必须是整数。") from exc
        if parsed < minimum:
            raise ValueError(f"{field_name} 必须大于等于 {minimum}。")
        return parsed

    @staticmethod
    def normalize_hex_color(value: str) -> str:
        color_text = value.strip()
        if color_text.startswith("#"):
            color_text = color_text[1:]

        if len(color_text) != 6 or any(character not in "0123456789abcdefABCDEF" for character in color_text):
            raise ValueError("颜色必须是 6 位十六进制数值，例如 66CCFF 或 #66CCFF。")

        return f"#{color_text.lower()}"

    @classmethod
    def parse_optional_hex_color(cls, value: str, field_name: str) -> str | None:
        if not value.strip():
            return None

        try:
            return cls.normalize_hex_color(value)
        except ValueError as exc:
            raise ValueError(f"{field_name}格式无效：{exc}") from exc

    @staticmethod
    def parse_optional_text(value: str) -> str | None:
        text = value.strip()
        return text or None

    @classmethod
    def parse_optional_font_family(cls, value: str) -> str | None:
        text = cls.parse_optional_text(value)
        if text in (None, "自动"):
            return None
        return text

    def parse_time_tick_density(self) -> int:
        density_value = int(round(float(self.time_density_var.get())))
        if density_value < MIN_TIME_TICK_DENSITY:
            density_value = MIN_TIME_TICK_DENSITY
        if density_value > MAX_TIME_TICK_DENSITY:
            density_value = MAX_TIME_TICK_DENSITY
        return density_value

    def parse_fixed_time_interval_seconds(self) -> int | None:
        unit_text = self.fixed_time_interval_unit_var.get().strip() or "自动"
        multiplier = TIME_INTERVAL_UNIT_CHOICES.get(unit_text)
        interval_text = self.fixed_time_interval_var.get().strip()
        if multiplier is None or not interval_text:
            return None

        try:
            interval_value = int(interval_text)
        except ValueError as exc:
            raise ValueError("固定时间间隔必须是正整数。") from exc

        if interval_value <= 0:
            raise ValueError("固定时间间隔必须大于 0。")

        return interval_value * multiplier


def launch_app() -> None:
    log_path = configure_runtime_logging()
    logger.info("Launching HWiNFOPlotterApp log_path=%s", log_path)
    app = HWiNFOPlotterApp()
    try:
        app.mainloop()
        logger.info("Application main loop exited normally")
    except Exception:
        logger.exception("Application main loop terminated unexpectedly")
        raise
