from __future__ import annotations

import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from tkinter import colorchooser, filedialog, messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from .core import (
    ChartStyle,
    DEFAULT_TIME_TICK_DENSITY,
    HWiNFOData,
    MAX_TIME_TICK_DENSITY,
    MIN_TIME_TICK_DENSITY,
    build_default_output_name,
    build_figure,
    format_elapsed_time,
    load_hwinfo_csv,
    save_figure,
)


LEGEND_LOCATION_CHOICES = {
    "自动": "best",
    "右上": "upper right",
    "左上": "upper left",
    "右下": "lower right",
    "左下": "lower left",
    "上方居中": "upper center",
    "下方居中": "lower center",
}
PREVIEW_MIN_DPI = 24
PREVIEW_PADDING = 24
TIME_INTERVAL_UNIT_CHOICES = {
    "自动": None,
    "秒": 1,
    "分钟": 60,
    "小时": 3600,
}


@dataclass(frozen=True)
class PreviewRenderRequest:
    request_id: int
    data: HWiNFOData
    selected_columns: tuple[int, ...]
    width_px: int
    height_px: int
    dpi: int
    style: ChartStyle
    color_by_column: dict[int, str]
    visible_range_seconds: tuple[float, float] | None = None


@dataclass(frozen=True)
class PreviewRenderResult:
    request_id: int
    figure: object | None = None
    error_message: str | None = None


@dataclass(frozen=True)
class PreloadSeriesRequest:
    request_id: int
    data: HWiNFOData


@dataclass(frozen=True)
class PreloadSeriesResult:
    request_id: int
    data: HWiNFOData
    error_message: str | None = None


class HWiNFOPlotterApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("HWiNFO CSV 折线图导出工具")
        self.geometry("1420x900")
        self.minsize(1180, 720)

        self.data: HWiNFOData | None = None
        self.visible_column_indices: list[int] = []
        self.selected_column_indices: set[int] = set()
        self.column_colors: dict[int, str] = {}
        self.preview_canvas: FigureCanvasTkAgg | None = None
        self.preview_figure = None
        self.preview_after_id: str | None = None
        self.preview_request_id = 0
        self.active_preview_request_id = 0
        self.last_preview_view_size: tuple[int, int] | None = None
        self.pending_preview_request: PreviewRenderRequest | None = None
        self.preview_results: Queue[PreviewRenderResult] = Queue()
        self.preview_request_lock = threading.Lock()
        self.preview_request_event = threading.Event()
        self.preview_shutdown_event = threading.Event()
        self.preview_worker = threading.Thread(target=self._preview_worker_loop, name="csv-visual-preview", daemon=True)
        self.preview_worker.start()
        self.preload_request_id = 0
        self.active_preload_request_id = 0
        self.pending_preload_request: PreloadSeriesRequest | None = None
        self.preload_results: Queue[PreloadSeriesResult] = Queue()
        self.preload_request_lock = threading.Lock()
        self.preload_request_event = threading.Event()
        self.preload_worker = threading.Thread(target=self._preload_worker_loop, name="csv-visual-preload", daemon=True)
        self.preload_worker.start()

        self.file_var = tk.StringVar(value=self._find_default_csv())
        self.filter_var = tk.StringVar()
        self.title_var = tk.StringVar()
        self.width_var = tk.StringVar(value="1920")
        self.height_var = tk.StringVar(value="1080")
        self.dpi_var = tk.StringVar(value="160")
        self.line_width_var = tk.StringVar(value="1.8")
        self.time_density_var = tk.DoubleVar(value=DEFAULT_TIME_TICK_DENSITY)
        self.time_density_label_var = tk.StringVar()
        self.fixed_time_interval_var = tk.StringVar()
        self.fixed_time_interval_unit_var = tk.StringVar(value="自动")
        self.trim_start_var = tk.DoubleVar(value=0.0)
        self.trim_end_var = tk.DoubleVar(value=0.0)
        self.trim_start_label_var = tk.StringVar(value="00:00:00")
        self.trim_end_label_var = tk.StringVar(value="00:00:00")
        self.trim_duration_label_var = tk.StringVar(value="可视化范围：00:00:00 → 00:00:00")
        self._updating_trim_controls = False
        self.show_grid_var = tk.BooleanVar(value=True)
        self.show_legend_var = tk.BooleanVar(value=True)
        self.legend_location_var = tk.StringVar(value="自动")
        self.selection_var = tk.StringVar(value="当前未选择参数")
        self.status_var = tk.StringVar(value="请选择一个 HWiNFO CSV 文件。")

        self.filter_var.trace_add("write", self._on_filter_changed)
        for option_var in (
            self.title_var,
            self.width_var,
            self.height_var,
            self.dpi_var,
            self.line_width_var,
            self.fixed_time_interval_var,
            self.fixed_time_interval_unit_var,
            self.show_grid_var,
            self.show_legend_var,
            self.legend_location_var,
        ):
            option_var.trace_add("write", self._on_chart_option_changed)
        self.time_density_var.trace_add("write", self._on_time_density_changed)
        self.trim_start_var.trace_add("write", self._on_trim_start_changed)
        self.trim_end_var.trace_add("write", self._on_trim_end_changed)
        self.update_time_density_label()

        self._build_layout()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(80, self.process_preview_results)

        if self.file_var.get():
            self.after(100, self.load_current_file)

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        file_frame = ttk.Frame(self, padding=(14, 14, 14, 10))
        file_frame.grid(row=0, column=0, sticky="ew")
        file_frame.columnconfigure(1, weight=1)

        ttk.Label(file_frame, text="CSV 文件").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(file_frame, textvariable=self.file_var).grid(row=0, column=1, sticky="ew")
        ttk.Button(file_frame, text="浏览...", command=self.browse_csv).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(file_frame, text="重新加载", command=self.load_current_file).grid(row=0, column=3, padx=(8, 0))

        paned = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        paned.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 10))

        control_panel_wrapper = ttk.Frame(paned)
        preview_panel = ttk.Frame(paned)
        control_panel_wrapper.columnconfigure(0, weight=1)
        control_panel_wrapper.rowconfigure(0, weight=1)
        preview_panel.columnconfigure(0, weight=1)
        preview_panel.rowconfigure(0, weight=1)

        paned.add(control_panel_wrapper, weight=1)
        paned.add(preview_panel, weight=3)

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

        options_frame = ttk.Labelframe(control_panel, text="图表设置", padding=12)
        options_frame.grid(row=5, column=0, sticky="ew")
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

        ttk.Label(options_frame, text="图例位置").grid(row=6, column=0, sticky="w", pady=(8, 0), padx=(0, 8))
        legend_location_box = ttk.Combobox(
            options_frame,
            textvariable=self.legend_location_var,
            values=list(LEGEND_LOCATION_CHOICES.keys()),
            state="readonly",
            width=10,
        )
        legend_location_box.grid(row=6, column=1, columnspan=3, sticky="ew", pady=(8, 0))

        trim_frame = ttk.Labelframe(control_panel, text="可视化范围", padding=12)
        trim_frame.grid(row=6, column=0, sticky="ew", pady=(12, 0))
        trim_frame.columnconfigure(1, weight=1)

        ttk.Label(trim_frame, text="起始裁剪").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.trim_start_scale = tk.Scale(
            trim_frame,
            from_=0,
            to=0,
            orient=tk.HORIZONTAL,
            resolution=1,
            showvalue=False,
            variable=self.trim_start_var,
            state=tk.DISABLED,
        )
        self.trim_start_scale.grid(row=0, column=1, sticky="ew")
        ttk.Label(trim_frame, textvariable=self.trim_start_label_var, width=10).grid(row=0, column=2, sticky="e", padx=(8, 0))

        ttk.Label(trim_frame, text="结束裁剪").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        self.trim_end_scale = tk.Scale(
            trim_frame,
            from_=0,
            to=0,
            orient=tk.HORIZONTAL,
            resolution=1,
            showvalue=False,
            variable=self.trim_end_var,
            state=tk.DISABLED,
        )
        self.trim_end_scale.grid(row=1, column=1, sticky="ew", pady=(8, 0))
        ttk.Label(trim_frame, textvariable=self.trim_end_label_var, width=10).grid(row=1, column=2, sticky="e", padx=(8, 0), pady=(8, 0))

        ttk.Label(trim_frame, textvariable=self.trim_duration_label_var).grid(
            row=2,
            column=0,
            columnspan=3,
            sticky="w",
            pady=(8, 0),
        )

        series_style_frame = ttk.Labelframe(control_panel, text="参数颜色", padding=12)
        series_style_frame.grid(row=7, column=0, sticky="nsew", pady=(12, 0))
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

        color_button_row = ttk.Frame(series_style_frame)
        color_button_row.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        color_button_row.columnconfigure((0, 1), weight=1)

        ttk.Button(color_button_row, text="设置颜色", command=self.choose_series_color).grid(
            row=0,
            column=0,
            sticky="ew",
            padx=(0, 4),
        )
        ttk.Button(color_button_row, text="清除颜色", command=self.clear_series_color).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(4, 0),
        )

        action_row = ttk.Frame(control_panel)
        action_row.grid(row=8, column=0, sticky="ew", pady=(12, 0))
        action_row.columnconfigure((0, 1), weight=1)

        ttk.Button(action_row, text="重置样式", command=self.reset_chart_options).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(action_row, text="导出透明 PNG", command=self.export_png).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        preview_frame = ttk.Labelframe(preview_panel, text="图表预览", padding=8)
        preview_frame.grid(row=0, column=0, sticky="nsew")
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)

        self.preview_host = ttk.Frame(preview_frame)
        self.preview_host.grid(row=0, column=0, sticky="nsew")
        self.preview_host.columnconfigure(0, weight=1)
        self.preview_host.rowconfigure(0, weight=1)
        self.preview_host.grid_propagate(False)
        self.preview_host.bind("<Configure>", self._on_preview_host_configure)

        self.preview_placeholder = ttk.Label(
            self.preview_host,
            text="加载 CSV 并选择参数后，图表会按当前配置自动预览。",
            anchor="center",
            justify="center",
        )
        self.preview_placeholder.grid(row=0, column=0, sticky="nsew")

        status_bar = ttk.Label(self, textvariable=self.status_var, relief=tk.SUNKEN, anchor="w", padding=(10, 6))
        status_bar.grid(row=2, column=0, sticky="ew")
        self._bind_mousewheel_events()

    def _find_default_csv(self) -> str:
        for pattern in ("*.csv", "*.CSV"):
            matches = list(Path.cwd().glob(pattern))
            if matches:
                return str(matches[0])
        return ""

    def _on_filter_changed(self, *_args) -> None:
        self.refresh_column_list()

    def _bind_mousewheel_events(self) -> None:
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind_all(sequence, self._on_mousewheel, add="+")

    def _on_mousewheel(self, event) -> str | None:
        units = self._get_mousewheel_units(event)
        if units == 0:
            return None

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
        self.schedule_preview_refresh()

    def _on_time_density_changed(self, *_args) -> None:
        self.update_time_density_label()
        self.schedule_preview_refresh()

    def update_time_density_label(self) -> None:
        density_value = self.parse_time_tick_density()
        self.time_density_label_var.set(f"{density_value}")

    def _on_trim_start_changed(self, *_args) -> None:
        if self._updating_trim_controls:
            return

        self._updating_trim_controls = True
        try:
            start_seconds = self.trim_start_var.get()
            end_seconds = self.trim_end_var.get()
            if start_seconds > end_seconds:
                self.trim_end_var.set(start_seconds)
            self.update_trim_labels()
        finally:
            self._updating_trim_controls = False

        if self.data:
            self.schedule_preview_refresh()

    def _on_trim_end_changed(self, *_args) -> None:
        if self._updating_trim_controls:
            return

        self._updating_trim_controls = True
        try:
            start_seconds = self.trim_start_var.get()
            end_seconds = self.trim_end_var.get()
            if end_seconds < start_seconds:
                self.trim_start_var.set(end_seconds)
            self.update_trim_labels()
        finally:
            self._updating_trim_controls = False

        if self.data:
            self.schedule_preview_refresh()

    def update_trim_labels(self) -> None:
        start_seconds = self.trim_start_var.get()
        end_seconds = self.trim_end_var.get()
        self.trim_start_label_var.set(format_elapsed_time(start_seconds))
        self.trim_end_label_var.set(format_elapsed_time(end_seconds))
        self.trim_duration_label_var.set(
            f"可视化范围：{format_elapsed_time(start_seconds)} → {format_elapsed_time(end_seconds)}"
        )

    def configure_trim_controls(self) -> None:
        total_duration_seconds = self.get_total_duration_seconds()
        slider_state = tk.NORMAL if total_duration_seconds > 0 else tk.DISABLED
        for slider in (self.trim_start_scale, self.trim_end_scale):
            slider.configure(from_=0, to=total_duration_seconds, state=slider_state)

        self._updating_trim_controls = True
        try:
            self.trim_start_var.set(0)
            self.trim_end_var.set(total_duration_seconds)
            self.update_trim_labels()
        finally:
            self._updating_trim_controls = False

    def get_total_duration_seconds(self) -> int:
        if not self.data or not self.data.elapsed_seconds:
            return 0
        return max(0, int(round(self.data.elapsed_seconds[-1])))

    def get_visible_range_seconds(self) -> tuple[float, float] | None:
        if not self.data or not self.data.elapsed_seconds:
            return None

        total_duration_seconds = self.data.elapsed_seconds[-1]
        start_seconds = max(0.0, min(float(self.trim_start_var.get()), total_duration_seconds))
        end_seconds = max(0.0, min(float(self.trim_end_var.get()), total_duration_seconds))
        if end_seconds < start_seconds:
            start_seconds, end_seconds = end_seconds, start_seconds
        return start_seconds, end_seconds

    def _on_control_host_configure(self, _event=None) -> None:
        scroll_region = self.control_scroll_canvas.bbox("all")
        if scroll_region is None:
            scroll_region = (0, 0, 0, 0)
        self.control_scroll_canvas.configure(scrollregion=scroll_region)

    def _on_control_canvas_configure(self, event=None) -> None:
        if event is None:
            return
        self.control_scroll_canvas.itemconfigure(self.control_window_id, width=event.width)

    def _on_preview_host_configure(self, event=None) -> None:
        if event is None:
            return

        current_size = (event.width, event.height)
        if current_size == self.last_preview_view_size:
            return

        self.last_preview_view_size = current_size
        if self.data and self.get_selected_columns():
            self.schedule_preview_refresh()

    def browse_csv(self) -> None:
        file_path = filedialog.askopenfilename(
            title="选择 HWiNFO CSV 文件",
            filetypes=[("CSV 文件", "*.csv;*.CSV"), ("所有文件", "*.*")],
        )
        if not file_path:
            return

        self.file_var.set(file_path)
        self.load_current_file()

    def load_current_file(self) -> None:
        file_text = self.file_var.get().strip()
        if not file_text:
            messagebox.showerror("未选择文件", "请先选择一个 CSV 文件。")
            return

        self.cancel_pending_preview_requests()
        self.cancel_pending_preload_requests()
        self.status_var.set("正在加载 CSV 并整理可用参数，请稍候...")
        self.update_idletasks()

        try:
            self.data = load_hwinfo_csv(file_text, preload_numeric=False)
        except Exception as exc:
            messagebox.showerror("加载失败", str(exc))
            self.status_var.set("CSV 加载失败。")
            return

        self.selected_column_indices.clear()
        self.column_colors.clear()
        if self.filter_var.get():
            self.filter_var.set("")
        else:
            self.refresh_column_list()
        self.refresh_selected_series_list()
        self.configure_trim_controls()
        self.clear_preview()
        self.start_background_preload(self.data)
        self.status_var.set(
            f"已加载 {self.data.source_path.name}，共 {len(self.data.timestamps)} 行有效数据，"
            f"{len(self.data.columns)} 个可选参数，编码：{self.data.encoding}。"
            "数值序列正在后台预载入内存，选择参数后会在后台自动预览。"
        )
        self.schedule_preview_refresh(immediate=True)

    def refresh_column_list(self) -> None:
        self.column_listbox.delete(0, tk.END)
        self.visible_column_indices.clear()

        if not self.data:
            self.selection_var.set("当前未选择参数")
            return

        keyword = self.filter_var.get().strip().lower()
        for column in self.data.columns:
            haystack = f"{column.name} {column.display_name}".lower()
            if keyword and keyword not in haystack:
                continue

            self.visible_column_indices.append(column.index)
            self.column_listbox.insert(tk.END, column.display_name)

        for listbox_index, column_index in enumerate(self.visible_column_indices):
            if column_index in self.selected_column_indices:
                self.column_listbox.selection_set(listbox_index)

        self.update_selection_label()

    def on_column_selection_changed(self, _event=None) -> None:
        visible_set = set(self.visible_column_indices)
        self.selected_column_indices -= visible_set

        for selected_position in self.column_listbox.curselection():
            self.selected_column_indices.add(self.visible_column_indices[selected_position])

        self.update_selection_label()
        self.refresh_selected_series_list()
        self.schedule_preview_refresh()

    def update_selection_label(self) -> None:
        count = len(self.selected_column_indices)
        if count == 0:
            self.selection_var.set("当前未选择参数")
        else:
            self.selection_var.set(f"当前已选择 {count} 个参数")

    def refresh_selected_series_list(self) -> None:
        self.selected_series_listbox.delete(0, tk.END)

        if not self.data:
            return

        selected_columns = [
            column
            for column in self.data.columns
            if column.index in self.selected_column_indices
        ]
        for listbox_index, column in enumerate(selected_columns):
            color_text = self.column_colors.get(column.index)
            display_text = column.display_name if not color_text else f"{column.display_name}  ·  {color_text}"
            self.selected_series_listbox.insert(tk.END, display_text)
            if color_text:
                self.selected_series_listbox.itemconfig(listbox_index, foreground=color_text)

    def choose_series_color(self) -> None:
        if not self.data:
            return

        selected_positions = self.selected_series_listbox.curselection()
        if not selected_positions:
            messagebox.showinfo("未选择参数", "请先在“参数颜色”列表中选择一个或多个参数。")
            return

        _, selected_color = colorchooser.askcolor(title="选择参数颜色")
        if not selected_color:
            return

        selected_columns = [
            column
            for column in self.data.columns
            if column.index in self.selected_column_indices
        ]
        for position in selected_positions:
            if position >= len(selected_columns):
                continue
            self.column_colors[selected_columns[position].index] = selected_color

        self.refresh_selected_series_list()
        self.schedule_preview_refresh(immediate=True)

    def clear_series_color(self) -> None:
        if not self.data:
            return

        selected_positions = self.selected_series_listbox.curselection()
        if not selected_positions:
            messagebox.showinfo("未选择参数", "请先在“参数颜色”列表中选择一个或多个参数。")
            return

        selected_columns = [
            column
            for column in self.data.columns
            if column.index in self.selected_column_indices
        ]
        changed = False
        for position in selected_positions:
            if position >= len(selected_columns):
                continue
            changed = self.column_colors.pop(selected_columns[position].index, None) is not None or changed

        if changed:
            self.refresh_selected_series_list()
            self.schedule_preview_refresh(immediate=True)

    def select_all_visible(self) -> None:
        if not self.visible_column_indices:
            return

        self.column_listbox.selection_set(0, tk.END)
        self.selected_column_indices.update(self.visible_column_indices)
        self.update_selection_label()
        self.refresh_selected_series_list()
        self.schedule_preview_refresh()

    def clear_selection(self) -> None:
        self.selected_column_indices.clear()
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
        self.show_grid_var.set(True)
        self.show_legend_var.set(True)
        self.legend_location_var.set("自动")
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

    def refresh_preview(self) -> None:
        self.preview_after_id = None
        if not self.data:
            self.cancel_pending_preview_requests()
            self.clear_preview()
            return

        if not self.get_selected_columns():
            self.cancel_pending_preview_requests()
            self.clear_preview()
            return

        try:
            preview_request = self.build_preview_request()
        except Exception as exc:
            self.status_var.set(f"自动预览未更新：{exc}")
            return

        self.enqueue_preview_request(preview_request)
        self.status_var.set("正在后台生成图表预览...")

    def export_png(self) -> None:
        if not self.data:
            messagebox.showerror("尚未加载", "请先加载一个 CSV 文件。")
            return

        selected_columns = self.get_selected_columns()
        if not selected_columns:
            messagebox.showerror("未选择参数", "请至少选择一个参数。")
            return

        default_name = build_default_output_name(self.data, selected_columns)
        output_path = filedialog.asksaveasfilename(
            title="导出透明 PNG",
            defaultextension=".png",
            initialfile=default_name,
            filetypes=[("PNG 文件", "*.png")],
        )
        if not output_path:
            return

        try:
            figure = self.build_current_figure(preview=False)
            destination = save_figure(figure, output_path)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))
            return

        self.status_var.set(f"已导出透明 PNG：{destination}")

    def build_current_figure(self, *, preview: bool = False):
        (
            selected_columns,
            width_px,
            height_px,
            dpi,
            style,
            color_by_column,
            visible_range_seconds,
        ) = self.collect_render_options(preview=preview)
        if not self.data:
            raise ValueError("请先加载一个 CSV 文件。")

        return build_figure(
            self.data,
            selected_columns,
            width_px=width_px,
            height_px=height_px,
            dpi=dpi,
            style=style,
            color_by_column=color_by_column,
            visible_range_seconds=visible_range_seconds,
        )

    def build_preview_request(self) -> PreviewRenderRequest:
        (
            selected_columns,
            width_px,
            height_px,
            dpi,
            style,
            color_by_column,
            visible_range_seconds,
        ) = self.collect_render_options(preview=True)
        if not self.data:
            raise ValueError("请先加载一个 CSV 文件。")

        self.preview_request_id += 1
        self.active_preview_request_id = self.preview_request_id
        return PreviewRenderRequest(
            request_id=self.preview_request_id,
            data=self.data,
            selected_columns=tuple(selected_columns),
            width_px=width_px,
            height_px=height_px,
            dpi=dpi,
            style=style,
            color_by_column=color_by_column,
            visible_range_seconds=visible_range_seconds,
        )

    def collect_render_options(
        self,
        *,
        preview: bool,
    ) -> tuple[list[int], int, int, int, ChartStyle, dict[int, str], tuple[float, float] | None]:
        selected_columns = self.get_selected_columns()
        if not selected_columns:
            raise ValueError("请至少选择一个参数。")

        width_px = self.parse_positive_int(self.width_var.get(), "宽度")
        height_px = self.parse_positive_int(self.height_var.get(), "高度")
        dpi = self.parse_positive_int(self.dpi_var.get(), "DPI")
        line_width = self.parse_positive_float(self.line_width_var.get(), "曲线线宽")
        style = ChartStyle(
            title=self.title_var.get().strip() or None,
            line_width=line_width,
            show_grid=self.show_grid_var.get(),
            show_legend=self.show_legend_var.get(),
            legend_location=LEGEND_LOCATION_CHOICES.get(self.legend_location_var.get(), "best"),
            time_tick_density=self.parse_time_tick_density(),
            fixed_time_interval_seconds=self.parse_fixed_time_interval_seconds(),
        )
        if preview:
            width_px, height_px, dpi = self.get_preview_render_options(width_px, height_px, dpi)
        color_by_column = {
            column_index: color_text
            for column_index, color_text in self.column_colors.items()
            if column_index in self.selected_column_indices
        }
        visible_range_seconds = self.get_visible_range_seconds()

        return selected_columns, width_px, height_px, dpi, style, color_by_column, visible_range_seconds

    def get_preview_render_options(self, width_px: int, height_px: int, dpi: int) -> tuple[int, int, int]:
        available_width = max(1, self.preview_host.winfo_width() - PREVIEW_PADDING)
        available_height = max(1, self.preview_host.winfo_height() - PREVIEW_PADDING)

        if available_width <= 1 or available_height <= 1:
            return width_px, height_px, dpi

        scale = min(
            1.0,
            available_width / width_px,
            available_height / height_px,
        )
        if scale >= 1.0:
            return width_px, height_px, dpi

        preview_dpi = max(PREVIEW_MIN_DPI, int(round(dpi * scale)))
        effective_scale = preview_dpi / dpi
        preview_width = max(200, int(round(width_px * effective_scale)))
        preview_height = max(200, int(round(height_px * effective_scale)))
        return preview_width, preview_height, preview_dpi

    def enqueue_preview_request(self, preview_request: PreviewRenderRequest) -> None:
        with self.preview_request_lock:
            self.pending_preview_request = preview_request
            self.preview_request_event.set()

    def cancel_pending_preview_requests(self) -> None:
        self.preview_request_id += 1
        self.active_preview_request_id = self.preview_request_id
        with self.preview_request_lock:
            self.pending_preview_request = None
            self.preview_request_event.clear()

    def start_background_preload(self, data: HWiNFOData) -> None:
        self.preload_request_id += 1
        preload_request = PreloadSeriesRequest(
            request_id=self.preload_request_id,
            data=data,
        )
        self.active_preload_request_id = preload_request.request_id
        with self.preload_request_lock:
            self.pending_preload_request = preload_request
            self.preload_request_event.set()

    def cancel_pending_preload_requests(self) -> None:
        self.preload_request_id += 1
        self.active_preload_request_id = self.preload_request_id
        with self.preload_request_lock:
            self.pending_preload_request = None
            self.preload_request_event.clear()

    def _preview_worker_loop(self) -> None:
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

            try:
                figure = build_figure(
                    preview_request.data,
                    preview_request.selected_columns,
                    width_px=preview_request.width_px,
                    height_px=preview_request.height_px,
                    dpi=preview_request.dpi,
                    style=preview_request.style,
                    color_by_column=preview_request.color_by_column,
                    visible_range_seconds=preview_request.visible_range_seconds,
                )
            except Exception as exc:
                self.preview_results.put(
                    PreviewRenderResult(
                        request_id=preview_request.request_id,
                        error_message=str(exc),
                    )
                )
                continue

            self.preview_results.put(
                PreviewRenderResult(
                    request_id=preview_request.request_id,
                    figure=figure,
                )
            )

    def _preload_worker_loop(self) -> None:
        while not self.preview_shutdown_event.is_set():
            self.preload_request_event.wait(0.1)
            if self.preview_shutdown_event.is_set():
                return
            if not self.preload_request_event.is_set():
                continue

            with self.preload_request_lock:
                preload_request = self.pending_preload_request
                self.pending_preload_request = None
                self.preload_request_event.clear()

            if preload_request is None:
                continue

            try:
                preload_request.data.preload_numeric_series()
            except Exception as exc:
                self.preload_results.put(
                    PreloadSeriesResult(
                        request_id=preload_request.request_id,
                        data=preload_request.data,
                        error_message=str(exc),
                    )
                )
                continue

            self.preload_results.put(
                PreloadSeriesResult(
                    request_id=preload_request.request_id,
                    data=preload_request.data,
                )
            )

    def process_preview_results(self) -> None:
        try:
            while True:
                preview_result = self.preview_results.get_nowait()
                if preview_result.request_id != self.active_preview_request_id:
                    if preview_result.figure is not None:
                        preview_result.figure.clear()
                    continue

                if preview_result.error_message is not None:
                    self.status_var.set(f"自动预览未更新：{preview_result.error_message}")
                    continue

                if preview_result.figure is not None:
                    self.show_figure(preview_result.figure)
                    self.status_var.set("图表预览已在后台更新。")
        except Empty:
            pass

        try:
            while True:
                preload_result = self.preload_results.get_nowait()
                if preload_result.request_id != self.active_preload_request_id:
                    continue
                if preload_result.data is not self.data:
                    continue

                if preload_result.error_message is not None:
                    if not self.get_selected_columns():
                        self.status_var.set(f"后台预载失败：{preload_result.error_message}")
                    continue

                if not self.get_selected_columns():
                    self.status_var.set("全部数值序列已在后台预载入内存，后续预览和导出会更快。")
        except Empty:
            pass
        finally:
            if not self.preview_shutdown_event.is_set():
                self.after(80, self.process_preview_results)

    def on_close(self) -> None:
        self.preview_shutdown_event.set()
        self.preview_request_event.set()
        self.preload_request_event.set()
        self.destroy()

    def get_selected_columns(self) -> list[int]:
        if not self.data:
            return []

        selected_set = set(self.selected_column_indices)
        return [column.index for column in self.data.columns if column.index in selected_set]

    def show_figure(self, figure) -> None:
        self.clear_preview()

        self.preview_placeholder.grid_remove()
        canvas = FigureCanvasTkAgg(figure, master=self.preview_host)
        canvas.draw()
        canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew")

        self.preview_canvas = canvas
        self.preview_figure = figure

    def clear_preview(self) -> None:
        if self.preview_canvas is not None:
            widget = self.preview_canvas.get_tk_widget()
            widget.destroy()
            self.preview_canvas = None

        if self.preview_figure is not None:
            self.preview_figure.clear()
        self.preview_figure = None
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
    app = HWiNFOPlotterApp()
    app.mainloop()
