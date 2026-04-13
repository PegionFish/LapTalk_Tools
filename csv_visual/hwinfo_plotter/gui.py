from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from .core import HWiNFOData, build_default_output_name, build_figure, load_hwinfo_csv, save_figure


class HWiNFOPlotterApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("HWiNFO CSV 折线图导出工具")
        self.geometry("1420x900")
        self.minsize(1180, 720)

        self.data: HWiNFOData | None = None
        self.visible_column_indices: list[int] = []
        self.selected_column_indices: set[int] = set()
        self.preview_canvas: FigureCanvasTkAgg | None = None
        self.preview_figure = None

        self.file_var = tk.StringVar(value=self._find_default_csv())
        self.filter_var = tk.StringVar()
        self.title_var = tk.StringVar()
        self.width_var = tk.StringVar(value="1920")
        self.height_var = tk.StringVar(value="1080")
        self.dpi_var = tk.StringVar(value="160")
        self.selection_var = tk.StringVar(value="当前未选择参数")
        self.status_var = tk.StringVar(value="请选择一个 HWiNFO CSV 文件。")

        self.filter_var.trace_add("write", self._on_filter_changed)

        self._build_layout()

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

        control_panel = ttk.Frame(paned, padding=(0, 0, 12, 0))
        preview_panel = ttk.Frame(paned)
        control_panel.columnconfigure(0, weight=1)
        control_panel.rowconfigure(2, weight=1)
        preview_panel.columnconfigure(0, weight=1)
        preview_panel.rowconfigure(0, weight=1)

        paned.add(control_panel, weight=1)
        paned.add(preview_panel, weight=3)

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

        options_frame = ttk.Labelframe(control_panel, text="导出设置", padding=12)
        options_frame.grid(row=5, column=0, sticky="ew")
        options_frame.columnconfigure(1, weight=1)

        ttk.Label(options_frame, text="图表标题").grid(row=0, column=0, sticky="w", pady=(0, 8), padx=(0, 8))
        ttk.Entry(options_frame, textvariable=self.title_var).grid(row=0, column=1, columnspan=3, sticky="ew", pady=(0, 8))

        ttk.Label(options_frame, text="宽度(px)").grid(row=1, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(options_frame, textvariable=self.width_var, width=10).grid(row=1, column=1, sticky="ew")

        ttk.Label(options_frame, text="高度(px)").grid(row=1, column=2, sticky="w", padx=(12, 8))
        ttk.Entry(options_frame, textvariable=self.height_var, width=10).grid(row=1, column=3, sticky="ew")

        ttk.Label(options_frame, text="DPI").grid(row=2, column=0, sticky="w", pady=(8, 0), padx=(0, 8))
        ttk.Entry(options_frame, textvariable=self.dpi_var, width=10).grid(row=2, column=1, sticky="ew", pady=(8, 0))

        action_row = ttk.Frame(control_panel)
        action_row.grid(row=6, column=0, sticky="ew", pady=(12, 0))
        action_row.columnconfigure((0, 1), weight=1)

        ttk.Button(action_row, text="预览图表", command=self.preview_plot).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(action_row, text="导出透明 PNG", command=self.export_png).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        preview_frame = ttk.Labelframe(preview_panel, text="图表预览", padding=8)
        preview_frame.grid(row=0, column=0, sticky="nsew")
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)

        self.preview_host = ttk.Frame(preview_frame)
        self.preview_host.grid(row=0, column=0, sticky="nsew")
        self.preview_host.columnconfigure(0, weight=1)
        self.preview_host.rowconfigure(0, weight=1)

        self.preview_placeholder = ttk.Label(
            self.preview_host,
            text="加载 CSV 并选择参数后，点击“预览图表”。",
            anchor="center",
            justify="center",
        )
        self.preview_placeholder.grid(row=0, column=0, sticky="nsew")

        status_bar = ttk.Label(self, textvariable=self.status_var, relief=tk.SUNKEN, anchor="w", padding=(10, 6))
        status_bar.grid(row=2, column=0, sticky="ew")

    def _find_default_csv(self) -> str:
        for pattern in ("*.csv", "*.CSV"):
            matches = list(Path.cwd().glob(pattern))
            if matches:
                return str(matches[0])
        return ""

    def _on_filter_changed(self, *_args) -> None:
        self.refresh_column_list()

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

        try:
            self.data = load_hwinfo_csv(file_text)
        except Exception as exc:
            messagebox.showerror("加载失败", str(exc))
            self.status_var.set("CSV 加载失败。")
            return

        self.selected_column_indices.clear()
        self.refresh_column_list()
        self.clear_preview()
        self.status_var.set(
            f"已加载 {self.data.source_path.name}，共 {len(self.data.timestamps)} 行有效数据，"
            f"{len(self.data.columns)} 个可选参数，编码：{self.data.encoding}。"
        )

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

    def update_selection_label(self) -> None:
        count = len(self.selected_column_indices)
        if count == 0:
            self.selection_var.set("当前未选择参数")
        else:
            self.selection_var.set(f"当前已选择 {count} 个参数")

    def select_all_visible(self) -> None:
        if not self.visible_column_indices:
            return

        self.column_listbox.selection_set(0, tk.END)
        self.selected_column_indices.update(self.visible_column_indices)
        self.update_selection_label()

    def clear_selection(self) -> None:
        self.selected_column_indices.clear()
        self.column_listbox.selection_clear(0, tk.END)
        self.update_selection_label()

    def preview_plot(self) -> None:
        try:
            figure = self.build_current_figure()
        except Exception as exc:
            messagebox.showerror("预览失败", str(exc))
            return

        self.show_figure(figure)
        self.status_var.set("图表预览已更新。")

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
            figure = self.build_current_figure()
            destination = save_figure(figure, output_path)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))
            return

        self.status_var.set(f"已导出透明 PNG：{destination}")

    def build_current_figure(self):
        if not self.data:
            raise ValueError("请先加载一个 CSV 文件。")

        selected_columns = self.get_selected_columns()
        if not selected_columns:
            raise ValueError("请至少选择一个参数。")

        width_px = self.parse_positive_int(self.width_var.get(), "宽度")
        height_px = self.parse_positive_int(self.height_var.get(), "高度")
        dpi = self.parse_positive_int(self.dpi_var.get(), "DPI")

        return build_figure(
            self.data,
            selected_columns,
            title=self.title_var.get().strip() or None,
            width_px=width_px,
            height_px=height_px,
            dpi=dpi,
        )

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


def launch_app() -> None:
    app = HWiNFOPlotterApp()
    app.mainloop()
