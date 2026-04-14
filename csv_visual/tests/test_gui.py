from __future__ import annotations

import base64
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hwinfo_plotter.core import HWiNFOData, LoadedCsvSession, SensorColumn, SeriesKey
from hwinfo_plotter.gui import HWiNFOPlotterApp, PreloadSeriesResult, fit_size_within_bounds


TEST_PREVIEW_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABpfZFQAAAAABJRU5ErkJggg=="
)


def build_synthetic_data(name: str = "RunA.csv", *, base_value: float = 1.0) -> HWiNFOData:
    base_time = datetime(2026, 4, 13, 12, 0, 0)
    return HWiNFOData(
        source_path=Path(name),
        encoding="utf-8",
        headers=["Date", "Time", "CPU", "GPU"],
        columns=[
            SensorColumn(index=2, name="CPU", occurrence=1, display_name="[002] CPU"),
            SensorColumn(index=3, name="GPU", occurrence=1, display_name="[003] GPU"),
        ],
        timestamps=[base_time + timedelta(seconds=index) for index in range(4)],
        rows=[
            ["13/04/2026", "12:00:00", f"{base_value + 0:.1f}", f"{base_value + 10:.1f}"],
            ["13/04/2026", "12:00:01", f"{base_value + 1:.1f}", f"{base_value + 11:.1f}"],
            ["13/04/2026", "12:00:02", f"{base_value + 2:.1f}", f"{base_value + 12:.1f}"],
            ["13/04/2026", "12:00:03", f"{base_value + 3:.1f}", f"{base_value + 13:.1f}"],
        ],
    )


def build_session(
    session_id: str,
    alias: str,
    *,
    offset_seconds: float = 0.0,
    is_reference: bool = False,
    base_value: float = 1.0,
    source_trim_start_seconds: float = 0.0,
    source_trim_end_seconds: float | None = None,
) -> LoadedCsvSession:
    return LoadedCsvSession(
        session_id=session_id,
        alias=alias,
        data=build_synthetic_data(f"{alias}.csv", base_value=base_value),
        offset_seconds=offset_seconds,
        is_reference=is_reference,
        source_trim_start_seconds=source_trim_start_seconds,
        source_trim_end_seconds=source_trim_end_seconds,
    )


def timeline_item_center(app: HWiNFOPlotterApp, item_id: int) -> tuple[int, int]:
    x1, y1, x2, y2 = app.timeline_canvas.bbox(item_id)
    return int((x1 + x2) / 2), int((y1 + y2) / 2)


def timeline_hit_item(app: HWiNFOPlotterApp, action: str, session_id: str | None = None) -> int:
    for item_id, hit in app.timeline_hit_regions.items():
        if hit == (action, session_id):
            return item_id
    raise AssertionError(f"未找到时间轴命中区域：{action}, {session_id}")


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

    def test_pick_csv_drop_paths_keeps_all_csv_and_deduplicates(self) -> None:
        base_dir = Path.cwd()
        first_csv = str(base_dir / "RunA.csv")
        second_csv = str(base_dir / "RunB.CSV")

        self.assertEqual(
            HWiNFOPlotterApp.pick_csv_drop_paths(
                (
                    str(base_dir / "notes.txt"),
                    first_csv,
                    second_csv,
                    first_csv,
                )
            ),
            (first_csv, second_csv),
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

    def test_four_module_layout_containers_exist(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            app.withdraw()

            self.assertTrue(app.file_management_module.winfo_exists())
            self.assertTrue(app.preview_module.winfo_exists())
            self.assertTrue(app.parameter_chart_module.winfo_exists())
            self.assertTrue(app.time_editing_module.winfo_exists())
            self.assertTrue(app.timeline_canvas.winfo_exists())
            self.assertEqual(app.file_management_module.cget("text"), "文件管理")
            self.assertEqual(app.preview_module.cget("text"), "图表预览")
            self.assertEqual(app.parameter_chart_module.cget("text"), "参数与图表设置")
            self.assertEqual(app.time_editing_module.cget("text"), "时间轴与可视范围")
        finally:
            app.on_close()

    def test_layout_prioritizes_preview_area(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            app.update()

            self.assertEqual(int(app.grid_columnconfigure(0)["weight"]), 3)
            self.assertEqual(int(app.grid_columnconfigure(1)["weight"]), 7)
            self.assertEqual(int(app.left_column.grid_rowconfigure(0)["weight"]), 2)
            self.assertEqual(int(app.left_column.grid_rowconfigure(1)["weight"]), 8)
            self.assertEqual(int(app.right_column.grid_rowconfigure(0)["weight"]), 8)
            self.assertEqual(int(app.right_column.grid_rowconfigure(1)["weight"]), 2)
            self.assertGreater(app.preview_module.winfo_width(), app.file_management_module.winfo_width())
            self.assertGreater(app.parameter_chart_module.winfo_height(), app.file_management_module.winfo_height())
            self.assertGreater(app.preview_module.winfo_height(), app.time_editing_module.winfo_height())
        finally:
            app.on_close()

    def test_file_management_tree_is_compact_and_has_no_offset_editors(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            self.assertEqual(app.session_tree.cget("columns"), ("filename", "alias", "duration"))
            self.assertIsNone(app.session_alias_entry)
            self.assertIsNone(app.session_offset_entry)
        finally:
            app.on_close()

    def test_timeline_toolbar_uses_reset_buttons_without_work_area_button(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            button_texts = [
                child.cget("text")
                for child in app.time_editing_module.winfo_children()[0].winfo_children()
                if child.winfo_class() == "TButton"
            ]

            self.assertEqual(button_texts, ["重置所选对齐", "重置所选裁剪"])
        finally:
            app.on_close()

    def test_timeline_default_zoom_fits_canvas_width(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.sessions = [build_session("run_a", "RunA", is_reference=True)]
            app.refresh_after_session_change(
                preferred_selection=["run_a"],
                preserve_trim_range=False,
                refresh_preview=False,
            )
            app.update_idletasks()
            app.refresh_timeline()

            metrics = app._get_timeline_metrics()
            span_seconds = app.timeline_end_seconds - app.timeline_start_seconds
            expected_pixels_per_second = (
                max(float(app.timeline_canvas.winfo_width()), 640.0) - metrics["left_gutter"] - metrics["right_padding"]
            ) / span_seconds

            self.assertAlmostEqual(app.timeline_zoom_factor, 1.0)
            self.assertAlmostEqual(app.timeline_pixels_per_second, expected_pixels_per_second, places=4)
        finally:
            app.on_close()

    def test_mousewheel_on_timeline_adjusts_zoom_factor(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.sessions = [build_session("run_a", "RunA", is_reference=True)]
            app.refresh_after_session_change(
                preferred_selection=["run_a"],
                preserve_trim_range=False,
                refresh_preview=False,
            )
            app.update_idletasks()
            app.refresh_timeline()
            before_zoom_factor = app.timeline_zoom_factor
            event = SimpleNamespace(widget=app.timeline_canvas, delta=120, num=None, state=0, x=60, y=60)

            result = app._on_mousewheel(event)

            self.assertEqual(result, "break")
            self.assertGreater(app.timeline_zoom_factor, before_zoom_factor)
        finally:
            app.on_close()

    def test_session_tree_selection_refreshes_timeline_clip_highlight(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.sessions = [
                build_session("run_a", "RunA", is_reference=True),
                build_session("run_b", "RunB", base_value=5.0),
            ]
            app.refresh_after_session_change(
                preferred_selection=["run_b"],
                preserve_trim_range=False,
                refresh_preview=False,
            )
            app.update_idletasks()
            app.refresh_timeline()

            body_id = app.timeline_clip_item_by_session_id["run_b"]

            self.assertEqual(app.timeline_canvas.itemcget(body_id, "width"), "2.0")
        finally:
            app.on_close()

    def test_clicking_timeline_clip_selects_session_tree_row(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.sessions = [
                build_session("run_a", "RunA", is_reference=True),
                build_session("run_b", "RunB", base_value=5.0),
            ]
            app.refresh_after_session_change(
                preferred_selection=["run_a"],
                preserve_trim_range=False,
                refresh_preview=False,
            )
            app.update_idletasks()
            app.refresh_timeline()
            x_position, y_position = timeline_item_center(app, app.timeline_clip_item_by_session_id["run_b"])

            result = app._on_timeline_button_press(SimpleNamespace(x=x_position, y=y_position, state=0))

            self.assertEqual(result, "break")
            self.assertEqual(app.get_selected_session_ids(), ("run_b",))
        finally:
            app.on_close()

    def test_dragging_timeline_clip_updates_offset(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.sessions = [build_session("run_a", "RunA", is_reference=True)]
            app.refresh_after_session_change(
                preferred_selection=["run_a"],
                preserve_trim_range=False,
                refresh_preview=False,
            )
            app.update_idletasks()
            app.refresh_timeline()
            x_position, y_position = timeline_item_center(app, app.timeline_clip_item_by_session_id["run_a"])

            with patch.object(app, "_schedule_timeline_preview_refresh") as mock_refresh:
                app._on_timeline_button_press(SimpleNamespace(x=x_position, y=y_position, state=0))
                app._on_timeline_drag(
                    SimpleNamespace(
                        x=x_position + int(app.timeline_pixels_per_second * 2.2),
                        y=y_position,
                        state=0,
                    )
                )

            self.assertEqual(app.sessions[0].offset_seconds, 2.0)
            mock_refresh.assert_called()
        finally:
            app.on_close()

    def test_dragging_selected_timeline_clip_moves_multi_selection(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.sessions = [
                build_session("run_a", "RunA", is_reference=True),
                build_session("run_b", "RunB", offset_seconds=5.0, base_value=5.0),
            ]
            app.refresh_after_session_change(
                preferred_selection=["run_a", "run_b"],
                preserve_trim_range=False,
                refresh_preview=False,
            )
            app.update_idletasks()
            app.refresh_timeline()
            x_position, y_position = timeline_item_center(app, app.timeline_clip_item_by_session_id["run_b"])

            with patch.object(app, "_schedule_timeline_preview_refresh"):
                app._on_timeline_button_press(SimpleNamespace(x=x_position, y=y_position, state=0))
                app._on_timeline_drag(
                    SimpleNamespace(
                        x=x_position + int(app.timeline_pixels_per_second * 1.2),
                        y=y_position,
                        state=0,
                    )
                )

            self.assertEqual([session.offset_seconds for session in app.sessions], [1.0, 6.0])
        finally:
            app.on_close()

    def test_dragging_timeline_clip_edges_updates_source_trim(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.sessions = [build_session("run_a", "RunA", is_reference=True)]
            app.refresh_after_session_change(
                preferred_selection=["run_a"],
                preserve_trim_range=False,
                refresh_preview=False,
            )
            app.update_idletasks()
            app.refresh_timeline()

            left_x, left_y = timeline_item_center(app, timeline_hit_item(app, "trim_left", "run_a"))
            with patch.object(app, "_schedule_timeline_preview_refresh"):
                app._on_timeline_button_press(SimpleNamespace(x=left_x, y=left_y, state=0))
                app._on_timeline_drag(
                    SimpleNamespace(
                        x=left_x + int(app.timeline_pixels_per_second * 1.2),
                        y=left_y,
                        state=0,
                    )
                )

            self.assertEqual(app.sessions[0].source_trim_start_seconds, 1.0)

            right_x, right_y = timeline_item_center(app, timeline_hit_item(app, "trim_right", "run_a"))
            with patch.object(app, "_schedule_timeline_preview_refresh"):
                app._on_timeline_button_press(SimpleNamespace(x=right_x, y=right_y, state=0))
                app._on_timeline_drag(
                    SimpleNamespace(
                        x=right_x - int(app.timeline_pixels_per_second * 1.2),
                        y=right_y,
                        state=0,
                    )
                )

            self.assertEqual(app.sessions[0].source_trim_end_seconds, 2.0)
        finally:
            app.on_close()

    def test_dragging_timeline_work_area_handle_updates_visible_range(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.sessions = [build_session("run_a", "RunA", is_reference=True)]
            app.refresh_after_session_change(
                preferred_selection=["run_a"],
                preserve_trim_range=False,
                refresh_preview=False,
            )
            app.update_idletasks()
            app.refresh_timeline()
            x_position, y_position = timeline_item_center(app, timeline_hit_item(app, "work_area_start"))

            with patch.object(app, "_schedule_timeline_preview_refresh"):
                app._on_timeline_button_press(SimpleNamespace(x=x_position, y=y_position, state=0))
                app._on_timeline_drag(
                    SimpleNamespace(
                        x=x_position + int(app.timeline_pixels_per_second * 1.2),
                        y=y_position,
                        state=0,
                    )
                )

            self.assertEqual(app.get_visible_range_seconds(), (1.0, 3.0))
        finally:
            app.on_close()

    def test_timeline_release_triggers_immediate_preview_refresh(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.timeline_drag_state = {"action": "move_clip"}

            with patch.object(app, "schedule_preview_refresh") as mock_refresh:
                result = app._on_timeline_button_release()

            self.assertEqual(result, "break")
            mock_refresh.assert_called_once_with(immediate=True)
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

    def test_add_csv_files_loads_multiple_sessions_and_flattens_series(self) -> None:
        data_a = build_synthetic_data("RunA.csv", base_value=1.0)
        data_b = build_synthetic_data("RunB.csv", base_value=5.0)

        app = HWiNFOPlotterApp()
        try:
            app.withdraw()

            with patch("hwinfo_plotter.gui.load_hwinfo_csv", side_effect=[data_a, data_b]) as mock_load, patch.object(
                app,
                "start_background_preload",
            ) as mock_preload:
                app.add_csv_files(
                    (
                        str(Path.cwd() / "RunA.csv"),
                        str(Path.cwd() / "RunB.csv"),
                    )
                )

            self.assertEqual(mock_load.call_count, 2)
            self.assertEqual(mock_preload.call_count, 2)
            self.assertEqual(len(app.sessions), 2)
            self.assertTrue(app.sessions[0].is_reference)
            self.assertFalse(app.sessions[1].is_reference)
            self.assertEqual(
                app.column_listbox.get(0, "end"),
                (
                    "[RunA] [002] CPU",
                    "[RunA] [003] GPU",
                    "[RunB] [002] CPU",
                    "[RunB] [003] GPU",
                ),
            )
        finally:
            app.on_close()

    def test_add_csv_files_skips_duplicate_paths(self) -> None:
        data_a = build_synthetic_data("RunA.csv", base_value=1.0)
        file_path = str(Path.cwd() / "RunA.csv")

        app = HWiNFOPlotterApp()
        try:
            app.withdraw()

            with patch("hwinfo_plotter.gui.load_hwinfo_csv", return_value=data_a) as mock_load, patch.object(
                app,
                "start_background_preload",
            ):
                app.add_csv_files((file_path,))
                app.add_csv_files((file_path,))

            self.assertEqual(mock_load.call_count, 1)
            self.assertEqual(len(app.sessions), 1)
        finally:
            app.on_close()

    def test_remove_selected_sessions_cleans_related_state(self) -> None:
        key_to_remove = SeriesKey("run_b", 2)

        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.sessions = [
                build_session("run_a", "RunA", is_reference=True),
                build_session("run_b", "RunB", base_value=5.0),
            ]
            app.selected_series_keys = {key_to_remove}
            app.series_colors = {key_to_remove: "#123456"}
            app.refresh_after_session_change(
                preferred_selection=["run_b"],
                preserve_trim_range=False,
                refresh_preview=False,
            )

            app.remove_selected_sessions()

            self.assertEqual([session.session_id for session in app.sessions], ["run_a"])
            self.assertEqual(app.selected_series_keys, set())
            self.assertEqual(app.series_colors, {})
        finally:
            app.on_close()

    def test_set_selected_session_as_reference_normalizes_offsets(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.sessions = [
                build_session("run_a", "RunA", is_reference=True),
                build_session("run_b", "RunB", offset_seconds=8.0, base_value=5.0),
            ]
            app.refresh_session_tree(preferred_selection=["run_b"])

            with patch.object(app, "schedule_preview_refresh") as mock_refresh:
                app.set_selected_session_as_reference()

            self.assertEqual([session.offset_seconds for session in app.sessions], [-8.0, 0.0])
            self.assertEqual([session.is_reference for session in app.sessions], [False, True])
            mock_refresh.assert_called_once_with(immediate=True)
        finally:
            app.on_close()

    def test_preview_request_keeps_export_size_sessions_and_series_colors(self) -> None:
        run_a = build_session("run_a", "RunA", is_reference=True)
        run_b = build_session("run_b", "RunB", offset_seconds=5.0, base_value=5.0)
        key_a = SeriesKey("run_a", 2)
        key_b = SeriesKey("run_b", 2)

        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.sessions = [run_a, run_b]
            app.selected_series_keys = {key_a, key_b}
            app.series_colors = {key_b: "#123456"}
            app.refresh_after_session_change(
                preferred_selection=["run_a"],
                preserve_trim_range=False,
                refresh_preview=False,
            )
            app.width_var.set("1600")
            app.height_var.set("900")
            app.dpi_var.set("144")
            app.title_var.set("预览测试")
            app.time_density_var.set(10)
            app.fixed_time_interval_var.set("2")
            app.fixed_time_interval_unit_var.set("分钟")
            app.trim_start_var.set(1)
            app.trim_end_var.set(6)
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
            self.assertEqual(preview_request.sessions, (run_a, run_b))
            self.assertEqual(preview_request.selected_series, (key_a, key_b))
            self.assertEqual(preview_request.color_by_series, {key_b: "#123456"})
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
            self.assertEqual(preview_request.visible_range_seconds, (1.0, 6.0))
        finally:
            app.on_close()

    def test_apply_series_color_uses_hex_entry_value(self) -> None:
        key = SeriesKey("run_a", 2)

        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.sessions = [build_session("run_a", "RunA", is_reference=True)]
            app.selected_series_keys = {key}
            app.refresh_after_session_change(
                preferred_selection=["run_a"],
                preserve_trim_range=False,
                refresh_preview=False,
            )
            app.selected_series_listbox.selection_set(0)
            app.series_color_var.set("66CCFF")

            with patch.object(app, "schedule_preview_refresh") as mock_refresh:
                app.apply_series_color()

            self.assertEqual(app.series_colors, {key: "#66ccff"})
            self.assertEqual(app.series_color_var.get(), "66CCFF")
            self.assertEqual(app.selected_series_listbox.get(0), "[002] CPU  ·  #66CCFF")
            mock_refresh.assert_called_once_with(immediate=True)
        finally:
            app.on_close()

    def test_apply_series_color_rejects_invalid_hex_value(self) -> None:
        key = SeriesKey("run_a", 2)

        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.sessions = [build_session("run_a", "RunA", is_reference=True)]
            app.selected_series_keys = {key}
            app.refresh_after_session_change(
                preferred_selection=["run_a"],
                preserve_trim_range=False,
                refresh_preview=False,
            )
            app.selected_series_listbox.selection_set(0)
            app.series_color_var.set("GGHHII")

            with patch("hwinfo_plotter.gui.messagebox.showerror") as mock_error, patch.object(
                app,
                "schedule_preview_refresh",
            ) as mock_refresh:
                app.apply_series_color()

            self.assertEqual(app.series_colors, {})
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
        key_a = SeriesKey("run_a", 2)
        key_b = SeriesKey("run_a", 3)

        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.sessions = [build_session("run_a", "RunA", is_reference=True)]
            app.selected_series_keys = {key_a, key_b}
            app.refresh_after_session_change(
                preferred_selection=["run_a"],
                preserve_trim_range=False,
                refresh_preview=False,
            )

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

    def test_handle_dropped_files_loads_all_csv_paths(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            app.withdraw()

            with patch.object(app, "add_csv_files") as mock_add:
                app.handle_dropped_files(
                    (
                        str(Path.cwd() / "notes.txt"),
                        str(Path.cwd() / "RunA.csv"),
                        str(Path.cwd() / "RunB.csv"),
                    )
                )

            mock_add.assert_called_once_with(
                (
                    str(Path.cwd() / "RunA.csv"),
                    str(Path.cwd() / "RunB.csv"),
                )
            )
        finally:
            app.on_close()

    def test_process_dropped_files_dispatches_queued_paths(self) -> None:
        class FakeDropManager:
            def __init__(self) -> None:
                self.queued_paths = [
                    (
                        str(Path.cwd() / "RunA.csv"),
                        str(Path.cwd() / "RunB.csv"),
                    ),
                    None,
                ]

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

            mock_handle.assert_called_once_with(
                (
                    str(Path.cwd() / "RunA.csv"),
                    str(Path.cwd() / "RunB.csv"),
                )
            )
            mock_schedule.assert_called_once_with()
        finally:
            app.on_close()

    def test_handle_dropped_files_reports_missing_csv(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            app.withdraw()

            with patch("hwinfo_plotter.gui.messagebox.showerror") as mock_error, patch.object(
                app,
                "add_csv_files",
            ) as mock_add:
                app.handle_dropped_files((str(Path.cwd() / "notes.txt"),))

            mock_error.assert_called_once_with("未找到 CSV", "请拖入 .csv 或 .CSV 文件。")
            mock_add.assert_not_called()
        finally:
            app.on_close()

    def test_process_preview_results_marks_preload_completion_without_selection(self) -> None:
        app = HWiNFOPlotterApp()
        try:
            app.withdraw()
            app.sessions = [build_session("run_a", "RunA", is_reference=True)]
            app.refresh_session_tree(preferred_selection=["run_a"])
            app.preload_results.put(PreloadSeriesResult(request_id=3, session_id="run_a"))

            with patch.object(app, "after"):
                app.process_preview_results()

            self.assertTrue(app.sessions[0].preload_ready)
            self.assertEqual(app.sessions[0].preload_error, None)
            self.assertEqual(app.status_var.get(), "RunA 的数值序列已在后台预载入内存。")
        finally:
            app.on_close()


if __name__ == "__main__":
    unittest.main()
