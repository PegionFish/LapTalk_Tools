from __future__ import annotations

import base64
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hwinfo_plotter.core import HWiNFOData, SensorColumn
from hwinfo_plotter.gui import HWiNFOPlotterApp, PreloadSeriesResult, fit_size_within_bounds


TEST_PREVIEW_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABpfZFQAAAAABJRU5ErkJggg=="
)


def build_synthetic_data() -> HWiNFOData:
    base_time = datetime(2026, 4, 13, 12, 0, 0)
    return HWiNFOData(
        source_path=Path("synthetic.csv"),
        encoding="utf-8",
        headers=["Date", "Time", "CPU", "GPU"],
        columns=[
            SensorColumn(index=2, name="CPU", occurrence=1, display_name="[002] CPU"),
            SensorColumn(index=3, name="GPU", occurrence=1, display_name="[003] GPU"),
        ],
        timestamps=[base_time + timedelta(seconds=index) for index in range(3)],
        rows=[
            ["13/04/2026", "12:00:00", "1.0", "2.0"],
            ["13/04/2026", "12:00:01", "2.0", "3.0"],
            ["13/04/2026", "12:00:02", "3.0", "4.0"],
        ],
    )


class GuiBehaviorTests(unittest.TestCase):
    def test_normalize_hex_color_accepts_plain_hex_value(self) -> None:
        self.assertEqual(HWiNFOPlotterApp.normalize_hex_color("66CCFF"), "#66ccff")
        self.assertEqual(HWiNFOPlotterApp.normalize_hex_color("#123456"), "#123456")

    def test_parse_optional_hex_color_accepts_empty_default(self) -> None:
        self.assertIsNone(HWiNFOPlotterApp.parse_optional_hex_color("", "网格颜色"))
        self.assertEqual(HWiNFOPlotterApp.parse_optional_hex_color("66CCFF", "网格颜色"), "#66ccff")

    def test_parse_optional_text_strips_blank_values(self) -> None:
        self.assertIsNone(HWiNFOPlotterApp.parse_optional_text("   "))
        self.assertEqual(HWiNFOPlotterApp.parse_optional_text("  Microsoft YaHei  "), "Microsoft YaHei")

    def test_parse_optional_font_family_accepts_auto_default(self) -> None:
        self.assertIsNone(HWiNFOPlotterApp.parse_optional_font_family("自动"))
        self.assertEqual(HWiNFOPlotterApp.parse_optional_font_family("Microsoft YaHei"), "Microsoft YaHei")

    def test_pick_csv_drop_path_uses_first_csv_file(self) -> None:
        self.assertEqual(
            HWiNFOPlotterApp.pick_csv_drop_path(
                (
                    r"C:\logs\notes.txt",
                    r"C:\logs\R23-15.CSV",
                    r"C:\logs\other.csv",
                )
            ),
            r"C:\logs\R23-15.CSV",
        )

    def test_fit_size_within_bounds_preserves_aspect_ratio(self) -> None:
        self.assertEqual(fit_size_within_bounds(1600, 900, 800, 800), (800, 450))
        self.assertEqual(fit_size_within_bounds(900, 1600, 800, 800), (450, 800))

    def test_font_family_choices_start_with_auto(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            self.assertTrue(app.font_family_choices)
            self.assertEqual(app.font_family_choices[0], "自动")
        finally:
            app.on_close()

    def test_filter_list_has_usable_height_without_fullscreen(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            app.update()

            self.assertGreaterEqual(app.column_listbox.winfo_height(), 120)
            self.assertGreater(app.control_scroll_canvas.winfo_height(), 0)
        finally:
            app.on_close()

    def test_mousewheel_scrolls_control_panel_from_content_area(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            event = SimpleNamespace(widget=app.control_panel, delta=-120, num=None, state=0)

            with patch.object(app.control_scroll_canvas, "yview_scroll") as mock_scroll:
                result = app._on_mousewheel(event)

            mock_scroll.assert_called_once_with(1, "units")
            self.assertEqual(result, "break")
        finally:
            app.on_close()

    def test_mousewheel_scrolls_parameter_list_without_hovering_scrollbar(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            event = SimpleNamespace(widget=app.column_listbox, delta=-120, num=None, state=0)

            with patch.object(app.column_listbox, "yview_scroll") as mock_scroll:
                result = app._on_mousewheel(event)

            mock_scroll.assert_called_once_with(1, "units")
            self.assertEqual(result, "break")
        finally:
            app.on_close()

    def test_mousewheel_does_not_scroll_preview_area(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            event = SimpleNamespace(widget=app.preview_host, delta=-120, num=None, state=0)

            result = app._on_mousewheel(event)

            self.assertIsNone(result)
        finally:
            app.on_close()

    def test_preview_request_keeps_export_size_and_colors(self) -> None:
        data = build_synthetic_data()

        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.data = data
            app.selected_column_indices = {2}
            app.column_colors = {2: "#123456"}
            app.configure_trim_controls()
            app.width_var.set("1600")
            app.height_var.set("900")
            app.dpi_var.set("144")
            app.title_var.set("预览测试")
            app.time_density_var.set(10)
            app.fixed_time_interval_var.set("2")
            app.fixed_time_interval_unit_var.set("分钟")
            app.trim_start_var.set(1)
            app.trim_end_var.set(2)
            app.show_time_axis_var.set(False)
            app.show_value_axis_var.set(False)
            app.axis_color_var.set("111111")
            app.grid_color_var.set("222222")
            app.time_text_color_var.set("333333")
            app.value_text_color_var.set("444444")
            app.legend_text_color_var.set("555555")
            app.font_family_var.set("  Microsoft YaHei  ")

            preview_request = app.build_preview_request()

            self.assertEqual(preview_request.width_px, 1600)
            self.assertEqual(preview_request.height_px, 900)
            self.assertEqual(preview_request.dpi, 144)
            self.assertEqual(preview_request.color_by_column, {2: "#123456"})
            self.assertEqual(preview_request.style.title, "预览测试")
            self.assertEqual(preview_request.style.time_tick_density, 10)
            self.assertEqual(preview_request.style.fixed_time_interval_seconds, 120)
            self.assertFalse(preview_request.style.show_time_axis)
            self.assertFalse(preview_request.style.show_value_axis)
            self.assertEqual(preview_request.style.axis_color, "#111111")
            self.assertEqual(preview_request.style.grid_color, "#222222")
            self.assertEqual(preview_request.style.time_text_color, "#333333")
            self.assertEqual(preview_request.style.value_text_color, "#444444")
            self.assertEqual(preview_request.style.legend_text_color, "#555555")
            self.assertEqual(preview_request.style.font_family, "Microsoft YaHei")
            self.assertEqual(preview_request.visible_range_seconds, (1.0, 2.0))
        finally:
            app.on_close()

    def test_apply_series_color_uses_hex_entry_value(self) -> None:
        data = build_synthetic_data()

        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.data = data
            app.selected_column_indices = {2}
            app.refresh_selected_series_list()
            app.selected_series_listbox.selection_set(0)
            app.series_color_var.set("66CCFF")

            with patch.object(app, "schedule_preview_refresh") as mock_refresh:
                app.apply_series_color()

            self.assertEqual(app.column_colors, {2: "#66ccff"})
            self.assertEqual(app.series_color_var.get(), "66CCFF")
            self.assertEqual(app.selected_series_listbox.get(0), "[002] CPU  ·  #66CCFF")
            mock_refresh.assert_called_once_with(immediate=True)
        finally:
            app.on_close()

    def test_apply_series_color_rejects_invalid_hex_value(self) -> None:
        data = build_synthetic_data()

        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.data = data
            app.selected_column_indices = {2}
            app.refresh_selected_series_list()
            app.selected_series_listbox.selection_set(0)
            app.series_color_var.set("GGHHII")

            with patch("hwinfo_plotter.gui.messagebox.showerror") as mock_error, patch.object(
                app,
                "schedule_preview_refresh",
            ) as mock_refresh:
                app.apply_series_color()

            self.assertEqual(app.column_colors, {})
            mock_error.assert_called_once()
            mock_refresh.assert_not_called()
        finally:
            app.on_close()

    def test_choose_chart_option_color_uses_color_picker(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            app.withdraw()

            with patch("hwinfo_plotter.gui.colorchooser.askcolor", return_value=((102, 204, 255), "#66ccff")), patch.object(
                app,
                "schedule_preview_refresh",
            ) as mock_refresh:
                app.choose_chart_option_color(app.time_text_color_var, "时间文字颜色")

            self.assertEqual(app.time_text_color_var.get(), "66CCFF")
            mock_refresh.assert_called_once_with(immediate=True)
        finally:
            app.on_close()

    def test_clear_chart_option_color_clears_existing_value(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.legend_text_color_var.set("112233")

            with patch.object(app, "schedule_preview_refresh") as mock_refresh:
                app.clear_chart_option_color(app.legend_text_color_var)

            self.assertEqual(app.legend_text_color_var.get(), "")
            mock_refresh.assert_called_once_with(immediate=True)
        finally:
            app.on_close()

    def test_curve_only_mode_is_exclusive_with_chart_element_toggles(self) -> None:
        data = build_synthetic_data()

        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.data = data
            app.selected_column_indices = {2, 3}

            app.curve_only_mode_var.set(True)

            self.assertTrue(app.curve_only_mode_var.get())
            self.assertFalse(app.show_grid_var.get())
            self.assertFalse(app.show_legend_var.get())
            self.assertFalse(app.show_time_axis_var.get())
            self.assertFalse(app.show_value_axis_var.get())

            preview_request = app.build_preview_request()

            self.assertTrue(preview_request.style.curve_only_mode)
            self.assertFalse(preview_request.style.show_grid)
            self.assertFalse(preview_request.style.show_legend)
            self.assertFalse(preview_request.style.show_time_axis)
            self.assertFalse(preview_request.style.show_value_axis)

            app.show_grid_var.set(True)

            self.assertFalse(app.curve_only_mode_var.get())
            self.assertTrue(app.show_grid_var.get())
        finally:
            app.on_close()

    def test_show_preview_image_replaces_placeholder_with_png(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            with patch.object(app.preview_host, "winfo_width", return_value=200), patch.object(
                app.preview_host,
                "winfo_height",
                return_value=100,
            ):
                app.show_preview_image(TEST_PREVIEW_PNG_BYTES)
                app.update_idletasks()

            self.assertIsNotNone(app.preview_image)
            self.assertIsNotNone(app.preview_label)
            self.assertTrue(app.preview_label.winfo_exists())
            image_value = app.preview_label.cget("image")
            if isinstance(image_value, tuple):
                image_value = image_value[0]
            self.assertEqual(image_value, str(app.preview_image))
            self.assertEqual(app.preview_image.width(), 100)
            self.assertEqual(app.preview_image.height(), 100)
            self.assertFalse(app.preview_placeholder.winfo_ismapped())
        finally:
            app.on_close()

    def test_load_current_file_clears_stale_filter_and_lists_columns_immediately(self) -> None:
        data = build_synthetic_data()

        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.file_var.set("synthetic.csv")
            app.filter_var.set("missing")

            with patch.object(app, "start_background_preload") as mock_preload, patch(
                "hwinfo_plotter.gui.load_hwinfo_csv",
                return_value=data,
            ) as mock_load:
                app.load_current_file()

            mock_load.assert_called_once_with("synthetic.csv", preload_numeric=False)
            mock_preload.assert_called_once_with(data)
            self.assertEqual(app.filter_var.get(), "")
            self.assertEqual(app.visible_column_indices, [2, 3])
            self.assertEqual(app.column_listbox.get(0, "end"), ("[002] CPU", "[003] GPU"))
        finally:
            app.on_close()

    def test_handle_dropped_files_loads_first_csv_path(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            app.withdraw()

            with patch.object(app, "load_current_file") as mock_load:
                app.handle_dropped_files(
                    (
                        r"C:\logs\notes.txt",
                        r"C:\logs\R23-15.CSV",
                    )
                )

            self.assertEqual(app.file_var.get(), r"C:\logs\R23-15.CSV")
            mock_load.assert_called_once_with()
        finally:
            app.on_close()

    def test_process_dropped_files_dispatches_queued_paths(self) -> None:
        class FakeDropManager:
            def __init__(self) -> None:
                self.queued_paths = [(r"C:\logs\R23-15.CSV",), None]

            def pop_dropped_paths(self):
                return self.queued_paths.pop(0)

            def unregister(self) -> None:
                return None

        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.file_drop_manager = FakeDropManager()

            with patch.object(app, "handle_dropped_files") as mock_handle, patch.object(
                app,
                "schedule_file_drop_processing",
            ) as mock_schedule:
                app.process_dropped_files()

            mock_handle.assert_called_once_with((r"C:\logs\R23-15.CSV",))
            mock_schedule.assert_called_once_with()
        finally:
            app.on_close()

    def test_handle_dropped_files_reports_missing_csv(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            app.withdraw()

            with patch("hwinfo_plotter.gui.messagebox.showerror") as mock_error, patch.object(
                app,
                "load_current_file",
            ) as mock_load:
                app.handle_dropped_files((r"C:\logs\notes.txt",))

            mock_error.assert_called_once_with("未找到 CSV", "请拖入 .csv 或 .CSV 文件。")
            mock_load.assert_not_called()
        finally:
            app.on_close()

    def test_process_preview_results_reports_preload_completion_without_selection(self) -> None:
        data = build_synthetic_data()

        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.data = data
            app.active_preload_request_id = 3
            app.preload_results.put(PreloadSeriesResult(request_id=3, data=data))

            with patch.object(app, "after"):
                app.process_preview_results()

            self.assertEqual(app.status_var.get(), "全部数值序列已在后台预载入内存，后续预览和导出会更快。")
        finally:
            app.on_close()


if __name__ == "__main__":
    unittest.main()
