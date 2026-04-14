# `csv_visual` 四区 UI 重构与 PR 式时间轴实现方案

## 0. 文档定位

本文件是后续 Session 的**单一权威实施方案**，用于替代此前拆开的两份计划：

- `2026-04-14-csv-visual-offset-timeline-dragging.md`
- `2026-04-14-csv-visual-ui-rearchitecture.md`

后续开发默认只参考本文件推进完整 UI 重构和时间轴功能实现。

---

## 1. 背景

`csv_visual` 当前已经具备以下基础能力：

- 加载多个 HWiNFO CSV 文件
- 为每个文件设置 `offset_seconds`
- 设置基准文件并归一化 offset
- 选择跨文件参数并叠加预览
- 设置图表样式与参数颜色
- 通过全局可视化范围裁剪最终预览 / 导出区间
- 后台自动生成图表预览

但当前 UI 仍然存在两个主要问题：

1. **信息架构问题**
   - 文件管理、参数筛选、图表样式、可视范围裁剪、时间 offset 编辑混在同一套纵向控制区中。
   - 用户需要频繁上下滚动才能完成“导入 → 选参数 → 对齐 → 裁剪 → 预览 → 导出”的主路径。

2. **时间编辑问题**
   - 当前 offset 主要依靠输入框和 `-10s / -1s / +1s / +10s` 按钮。
   - 当前裁剪依靠独立的起止滑块。
   - 对齐和裁剪都属于时间维度编辑，但现在是分散的控件，不像非线性编辑软件那样能在同一条时间轴中完成。

因此本次计划将范围扩大为：

- 推倒当前 UI 布局，改为四区布局
- 将文件对齐和裁剪统一到类似 Adobe Premiere Pro 的拖动式时间轴中
- 利用现有实时预览能力，让用户拖动或伸缩 CSV clip 后快速看到图表结果

---

## 2. 总体目标

## 2.1 UI 目标

按用户提供的草图，将界面拆成四个稳定模块：

```text
+----------------------+--------------------------------------+
| 模块 1               | 模块 2                               |
| 文件管理             | 图表预览                             |
+----------------------+--------------------------------------+
| 模块 3               | 模块 4                               |
| 参数筛选 + 图表设置  | 可视化范围 + PR 式时间轴             |
+----------------------+--------------------------------------+
```

四个模块的语义如下：

- **模块 1**：当前会话中有哪些 CSV 文件
- **模块 2**：当前设置最终会渲染成什么图
- **模块 3**：要绘制哪些参数、使用什么图表样式
- **模块 4**：各 CSV 如何对齐、每个 CSV 取哪段、最终导出看哪段时间

## 2.2 功能目标

本次完整实现目标包括：

1. 将现有 UI 重构为四区布局
2. 将现有文件管理功能迁移到模块 1
3. 将现有图表预览迁移到模块 2
4. 将参数筛选、已选参数颜色、图表样式设置迁移到模块 3
5. 在模块 4 中实现统一的 PR 式时间轴
6. 在同一条时间轴中支持：
   - 拖动 CSV clip 主体调整文件 offset
   - 伸缩 CSV clip 左右边缘裁剪该 CSV 的有效时间段
   - 拖动全局工作区边界确定最终预览 / 导出时间范围
7. 拖动或伸缩过程中联动实时预览，并避免后台预览过度刷新
8. 更新 README 和测试，确保后续可维护

## 2.3 非目标

首版不做以下内容：

- 自动曲线匹配对齐
- 峰值 / 阈值磁吸
- 多轨分组、锁轨、静音轨
- 自由停靠面板或保存工作区布局
- 在图表预览 PNG 上直接拖动曲线实体
- 保存和恢复完整项目文件

---

## 3. 当前实现落点

当前关键代码位置：

- `csv_visual/hwinfo_plotter/gui.py`
  - `_build_layout()`：当前整体布局入口
  - `session_tree`：文件列表
  - `session_offset_entry`：当前 offset 输入框
  - `apply_selected_session_details()`：应用 alias / offset
  - `nudge_selected_session_offset()`：offset 快捷微调
  - `configure_trim_controls()`：当前全局可视范围滑块配置
  - `build_preview_request()`：预览请求组装
  - `schedule_preview_refresh()`：预览防抖刷新
- `csv_visual/hwinfo_plotter/core.py`
  - `LoadedCsvSession`
  - `align_series_x_values(...)`
  - `compute_global_time_bounds(...)`
  - `normalize_offsets_for_reference(...)`
  - `resolve_comparison_visible_range_seconds(...)`
  - `filter_visible_series(...)`
  - `build_comparison_figure(...)`
- `csv_visual/tests/test_core.py`
  - 需要补充 per-session trim 和统一时间轴相关核心测试
- `csv_visual/tests/test_gui.py`
  - 需要补充四区布局、时间轴拖动、clip 伸缩、预览联动测试
- `csv_visual/README.md`
  - 需要同步更新使用说明

---

## 4. 四区 UI 设计

## 4.1 顶层布局

建议使用顶层 `Panedwindow` 或 `grid` 权重组合实现：

- 左列：模块 1 + 模块 3
- 右列：模块 2 + 模块 4
- 右列宽度大于左列
- 模块 2 高度大于模块 4，但模块 4 必须保留足够时间轴空间

推荐默认窗口仍以当前 `1420x900` 为基准，同时保持当前 `1180x720` 最小窗口可用。

## 4.2 模块 1：文件管理

职责：

- 管理已加载 CSV 文件
- 展示文件级状态
- 提供精确 offset 数值编辑兜底

建议控件：

- 文件 `Treeview`
  - 别名
  - 文件名
  - 时长
  - offset
  - 有效片段范围
  - 基准
  - 预载状态
- 操作按钮
  - 添加 CSV
  - 移除选中
  - 清空全部
  - 设为基准
- 文件详情区
  - 别名输入
  - offset 输入
  - 可选：有效起止时间数值显示

与模块 4 的关系：

- 模块 1 负责“文件列表和精确数值”
- 模块 4 负责“拖动式时间编辑”
- 二者必须双向同步选中状态和 offset / trim 状态

## 4.3 模块 2：图表预览

职责：

- 稳定展示最终图表效果
- 不承担高频拖拽编辑

建议内容：

- 当前图表预览
- 空状态提示
- 预览状态提示
- 可选信息条：
  - 预览尺寸
  - DPI
  - 当前全局工作区范围
  - 已选参数数

模块 2 不直接承载时间轴编辑。时间轴交互发生在模块 4，模块 2 只负责展示交互后的实时结果。

## 4.4 模块 3：参数筛选和图表设置

职责：

- 参数筛选
- 参数选择
- 已选参数颜色
- 图表样式
- 导出静态参数

建议子区：

```text
参数与图表设置
├─ 参数筛选
│  ├─ 搜索框
│  └─ 参数列表
├─ 已选参数颜色
│  ├─ 已选列表
│  ├─ HEX 输入
│  └─ 应用 / 取色 / 清除
└─ 图表样式
   ├─ 标题 / 宽度 / 高度 / DPI / 线宽
   ├─ 时间刻度密度 / 固定时间间隔
   ├─ 网格 / 图例 / 时间轴 / 数值轴 / 纯曲线模式
   ├─ 图例位置
   ├─ 坐标轴 / 网格 / 文字颜色
   └─ 字体
```

模块 3 不再承载：

- 起始裁剪
- 结束裁剪
- offset 微调主操作
- 时间轴拖动

## 4.5 模块 4：统一时间编辑工作台

模块 4 是本次重构重点，负责全部时间相关编辑：

- 文件对齐
- 单个 CSV 有效片段裁剪
- 全局预览 / 导出范围裁剪
- 时间轴缩放与滚动

模块 4 不再是“上面两个裁剪滑块 + 下面一个时间轴”的分离模型，而是一个统一的 PR 式时间轴。

---

## 5. PR 式统一时间轴设计

## 5.1 核心交互模型

每个 CSV 文件在模块 4 中显示为一条 clip：

```text
时间刻度:  -10s        0s        10s        20s        30s
             |---------|---------|----------|----------|

RunA:        [==================== CSV clip ====================]
RunB:                [========== CSV clip ==========]
RunC:   [================ CSV clip ================]

全局工作区:        |---------------- preview / export ----------------|
```

clip 支持三类编辑：

1. **拖动 clip 主体**
   - 调整该 CSV 的 `offset_seconds`
   - 整个 CSV 在全局时间线上左移或右移

2. **拖动 clip 左右边缘**
   - 调整该 CSV 的有效源片段
   - 类似 PR 中 trim clip 的入点 / 出点
   - 用于裁掉单个 CSV 不需要参与比较的开头或结尾

3. **拖动全局工作区边界**
   - 调整最终图表预览和导出的全局时间范围
   - 替代当前独立的起始 / 结束裁剪滑块

## 5.2 三类时间概念

为避免实现和文案混乱，必须明确区分三类时间：

### 5.2.1 原始 CSV 时间

来自 CSV 的 `elapsed_seconds`：

```text
raw_elapsed_seconds
```

它始终不改写。

### 5.2.2 文件对齐时间

由 offset 决定：

```text
aligned_elapsed_seconds = raw_elapsed_seconds + offset_seconds
```

拖动 clip 主体只修改 `offset_seconds`。

### 5.2.3 有效片段与全局工作区

单个 CSV 的有效片段：

```text
source_trim_start_seconds <= raw_elapsed_seconds <= source_trim_end_seconds
```

全局预览 / 导出工作区：

```text
work_area_start_seconds <= aligned_elapsed_seconds <= work_area_end_seconds
```

最终真正参与绘图的数据点必须同时满足：

```text
raw_elapsed_seconds 在该 CSV 的有效片段内
aligned_elapsed_seconds 在全局工作区内
```

## 5.3 clip 的视觉表达

建议每条 CSV 轨道显示：

- 完整 CSV 原始长度的淡色背景
- 当前有效片段的高亮主体
- 左右 trim handle
- offset 文本
- alias / 文件名
- 基准文件标记
- 选中高亮

示意：

```text
RunA | ----[■■■■■■■■■■■■■■■■■■]----
           ^                  ^
           左 trim handle     右 trim handle
```

其中：

- 拖动中间高亮区域：移动 clip
- 拖动左边缘：调整该 CSV 的 `source_trim_start_seconds`
- 拖动右边缘：调整该 CSV 的 `source_trim_end_seconds`

## 5.4 全局工作区

全局工作区建议显示为覆盖所有轨道的半透明区域或顶部 range bar：

```text
Work Area |       [==============================]
RunA      | ----[■■■■■■■■■■■■■■■■■■]----
RunB      | --------[■■■■■■■■■■]---------
```

全局工作区对应现有：

- `trim_start_var`
- `trim_end_var`
- `visible_range_seconds`

区别在于：

- 旧设计使用独立滑块
- 新设计使用时间轴上的左右边界 handle

## 5.5 与实时预览的联动

因为现有已经支持后台实时预览，统一时间轴应该充分利用这一点：

- 拖动 clip 主体时，图表预览按新的 offset 更新
- 伸缩 clip 边缘时，图表预览按新的 CSV 有效片段更新
- 拖动全局工作区边界时，图表预览按新的导出范围更新

刷新策略：

1. 时间轴本地视觉必须实时更新
2. 图表预览在拖动过程中节流刷新
3. 鼠标释放时执行一次立即刷新

建议节流：

- 拖动过程中：`120ms ~ 180ms`
- 鼠标释放：`immediate=True`

## 5.6 多选拖动

支持基于模块 1 文件表格多选的联动拖动：

- 如果拖动的是已选 clip 之一，则所有选中文件整体平移
- 只改变这些文件的 `offset_seconds`
- 不改变各自的有效片段长度

首版不需要做时间轴框选。

## 5.7 吸附与精度

建议首版支持：

- 默认吸附到 `1s`
- 按住 `Shift` 吸附到 `0.1s`
- 后续可扩展 `Alt` 关闭吸附

吸附应同时作用于：

- clip 主体拖动
- clip 边缘伸缩
- 全局工作区边界拖动

---

## 6. 数据模型与核心逻辑调整

## 6.1 当前模型

当前 `LoadedCsvSession` 包含：

```python
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
```

当前全局裁剪通过 `visible_range_seconds` 传入渲染流程。

## 6.2 建议新增 per-session trim 字段

为了支持“伸缩 CSV clip 左右边缘裁剪该 CSV 有效片段”，建议扩展 `LoadedCsvSession`：

```python
source_trim_start_seconds: float = 0.0
source_trim_end_seconds: float | None = None
```

语义：

- `source_trim_start_seconds`：该 CSV 原始时间轴上的有效入点
- `source_trim_end_seconds`：该 CSV 原始时间轴上的有效出点
- `None` 表示使用 CSV 原始结尾

默认值保持完整文件参与比较，因此不会破坏现有行为。

## 6.3 有效边界计算

建议新增纯函数：

```python
def get_session_source_duration(session: LoadedCsvSession) -> float:
    ...

def resolve_session_source_trim_range(session: LoadedCsvSession) -> tuple[float, float]:
    ...

def compute_session_timeline_range(session: LoadedCsvSession) -> tuple[float, float]:
    ...

def compute_session_active_timeline_range(session: LoadedCsvSession) -> tuple[float, float]:
    ...
```

区别：

- `session_timeline_range`：完整 CSV 在全局时间轴上的范围
- `session_active_timeline_range`：trim 后有效片段在全局时间轴上的范围

## 6.4 渲染过滤顺序

建议 `filter_visible_series(...)` 的过滤顺序改为：

1. 读取原始 `x_values / y_values`
2. 按该 session 的 `source_trim_start_seconds / source_trim_end_seconds` 先过滤原始时间
3. 将剩余 x 值加上 `offset_seconds`
4. 再按全局 `visible_range_seconds` 过滤

这样可以同时支持：

- 单 CSV 片段裁剪
- 多 CSV 对齐
- 全局预览 / 导出范围裁剪

## 6.5 全局范围计算

`compute_global_time_bounds(...)` 建议基于 active range 计算，而不是始终基于完整 CSV 原始长度。

理由：

- 用户伸缩 CSV clip 后，时间轴和工作区边界应该围绕有效内容计算
- 可以避免被已裁掉的 CSV 开头 / 结尾撑大时间轴范围

如果后续希望仍能一键回到完整源范围，可在 UI 中提供“显示完整源范围”或“重置裁剪”。

## 6.6 向后兼容

默认情况下：

- `source_trim_start_seconds = 0.0`
- `source_trim_end_seconds = None`

等价于旧行为：

- 所有 CSV 全长参与比较
- 只通过 `offset_seconds` 对齐
- 只通过全局 `visible_range_seconds` 裁剪最终输出

---

## 7. GUI 实施落点

## 7.1 拆分 `_build_layout()`

建议将当前 `gui.py` 中的 `_build_layout()` 拆为：

```python
def _build_layout(self) -> None:
    self._build_root_layout()
    self._build_file_management_module()
    self._build_preview_module()
    self._build_parameter_and_chart_module()
    self._build_time_editing_module()
```

模块 4 再拆：

```python
def _build_time_editing_module(self) -> None:
    self._build_timeline_toolbar()
    self._build_timeline_canvas()
    self._build_timeline_status()
```

## 7.2 新增时间轴状态

建议新增 GUI 状态：

```python
self.timeline_canvas
self.timeline_hscrollbar
self.timeline_zoom_var
self.timeline_pixels_per_second
self.timeline_drag_state
self.timeline_clip_item_by_session_id
self.timeline_work_area_item_ids
```

拖拽状态建议记录：

- 操作类型：`move_clip` / `trim_left` / `trim_right` / `work_area_start` / `work_area_end`
- 起始鼠标位置
- 起始 offset
- 起始 source trim
- 起始 work area
- 参与联动的 session ids
- 当前吸附精度

## 7.3 新增时间轴方法

建议新增：

```python
def refresh_timeline(self) -> None: ...
def _draw_timeline_axis(self) -> None: ...
def _draw_timeline_work_area(self) -> None: ...
def _draw_timeline_sessions(self) -> None: ...
def _timeline_x_to_seconds(self, x: float) -> float: ...
def _timeline_seconds_to_x(self, seconds: float) -> float: ...
def _on_timeline_button_press(self, event) -> str | None: ...
def _on_timeline_drag(self, event) -> str | None: ...
def _on_timeline_button_release(self, event) -> str | None: ...
def _apply_timeline_session_updates(...) -> None: ...
def _apply_timeline_work_area_update(...) -> None: ...
def _schedule_timeline_preview_refresh(...) -> None: ...
```

## 7.4 旧 trim 控件处理

整体 UI 重构后，不建议继续保留独立的起始 / 结束裁剪滑块作为主控件。

推荐处理：

- 模块 4 中不再放旧的两个 `tk.Scale`
- 保留现有 `trim_start_var / trim_end_var` 作为底层状态
- 在时间轴上用全局工作区 handle 操作它们
- 可选提供小型数值输入 / 标签作为精确显示

---

## 8. 预览刷新策略

## 8.1 触发源

以下操作触发预览刷新：

- 模块 3 参数选择变化
- 模块 3 图表样式变化
- 模块 4 clip 主体拖动
- 模块 4 clip 左右边缘伸缩
- 模块 4 全局工作区边界调整
- 模块 1 offset 数值手输
- 模块 1 设为基准

## 8.2 拖动中的刷新

拖动或伸缩时：

- Canvas 立即重绘
- 模块 1 offset / trim 文本立即更新
- 图表预览防抖刷新

鼠标释放时：

- 取消挂起的低优先级预览刷新
- 立即提交一次最终预览请求

## 8.3 预览请求内容

`PreviewRenderRequest` 中的 `sessions` 应包含最新的：

- `offset_seconds`
- `source_trim_start_seconds`
- `source_trim_end_seconds`

`visible_range_seconds` 应来自全局工作区。

---

## 9. 分阶段实施计划

## Phase 1：四区布局骨架

目标：

- 先建立四区 UI，不改变核心行为

任务：

1. 重构顶层布局为模块 1 / 2 / 3 / 4
2. 将文件管理迁移到模块 1
3. 将预览区迁移到模块 2
4. 保持现有参数选择、裁剪和预览功能可用

验收：

- 程序启动后呈现四区布局
- 添加 CSV、选择参数、预览仍能正常工作

## Phase 2：模块 3 迁移与整理

目标：

- 将参数筛选和图表设置完整收敛到模块 3

任务：

1. 迁移参数筛选和参数列表
2. 迁移已选参数颜色区
3. 迁移图表样式区
4. 保持模块 3 内部可滚动

验收：

- 模块 3 只承担参数和图表样式职责
- 时间编辑控件不再混入模块 3

## Phase 3：模块 4 静态时间轴与全局工作区

目标：

- 在模块 4 建立统一时间轴视觉框架

任务：

1. 绘制时间刻度
2. 绘制每个 CSV clip
3. 绘制全局工作区
4. 将旧 `trim_start_var / trim_end_var` 映射为工作区边界
5. 支持模块 1 与模块 4 的选中同步

验收：

- 多文件能显示为多条 clip
- 全局工作区能替代旧裁剪滑块的显示语义

## Phase 4：clip 主体拖动实现文件对齐

目标：

- 支持类似 PR 的 clip 平移对齐

任务：

1. 点击 clip 选中文件
2. 拖动 clip 主体修改 `offset_seconds`
3. 支持多选整体平移
4. 支持吸附精度
5. 联动实时预览

验收：

- 拖动后模块 1 offset、模块 4 clip 位置、模块 2 预览一致

## Phase 5：clip 边缘伸缩实现单 CSV 裁剪

目标：

- 支持通过伸缩 CSV clip 确定单个 CSV 的有效时间段

任务：

1. 扩展 `LoadedCsvSession` 增加 source trim 字段
2. 增加核心纯函数解析 source trim
3. 修改 `filter_visible_series(...)` 支持 per-session trim
4. 时间轴支持拖动左右边缘
5. 模块 1 显示有效片段范围
6. 联动实时预览

验收：

- 伸缩某个 CSV clip 后，仅该 CSV 的有效数据段参与绘图
- 预览实时体现裁剪结果

## Phase 6：完善、测试与文档

目标：

- 完成可交付收尾

任务：

1. 更新时间轴缩放和横向滚动
2. 增加重置裁剪 / 重置对齐能力
3. 更新 README
4. 补充核心测试和 GUI 测试
5. 做手工回归

验收：

- 四区布局和 PR 式时间轴完整可用
- README 与测试覆盖新工作流

---

## 10. 测试计划

## 10.1 核心测试

建议补充 `csv_visual/tests/test_core.py`：

1. `LoadedCsvSession` 默认 source trim 等价于全长
2. per-session trim 会过滤该 CSV 的原始时间范围
3. `offset_seconds` 仍只负责对齐，不改原始时间
4. per-session trim 与全局 work area 同时生效
5. `compute_global_time_bounds(...)` 基于 active range 返回边界
6. 切换基准文件后，source trim 不应变化

## 10.2 GUI 测试

建议补充 `csv_visual/tests/test_gui.py`：

1. 启动后四个模块容器存在
2. 导入文件后模块 1 和模块 4 同步显示 session
3. 点击模块 1 文件会高亮模块 4 对应 clip
4. 点击模块 4 clip 会选中模块 1 文件
5. 拖动 clip 主体会更新 `offset_seconds`
6. 伸缩 clip 左右边缘会更新 source trim
7. 拖动全局工作区边界会更新 `visible_range_seconds`
8. 拖动释放后触发立即预览刷新

## 10.3 手工验证

建议使用 2~3 个 CSV 手工验证：

1. 导入多个 CSV
2. 选择至少两个跨文件参数
3. 拖动某个 CSV clip 完成对齐
4. 伸缩某个 CSV clip 裁掉不需要的开头或结尾
5. 拖动全局工作区确定最终导出范围
6. 观察模块 2 实时预览
7. 导出透明 PNG 并与预览核对

---

## 11. 风险与处理策略

## 11.1 风险：UI 重构范围大

处理：

- 分阶段推进
- 每个 Phase 后保证主流程可用
- 不把核心绘图逻辑和 UI 重构绑在同一步大改

## 11.2 风险：clip 伸缩引入 per-session trim 后影响渲染逻辑

处理：

- 先加纯函数和核心测试
- 默认 trim 等价于全长，保证旧行为不退化
- 再接 GUI 时间轴边缘拖动

## 11.3 风险：拖动实时预览导致卡顿

处理：

- Canvas 本地即时刷新
- 后台图表预览节流
- 鼠标释放后立即刷新最终结果

## 11.4 风险：模块 4 信息过密

处理：

- 时间轴区域优先保证高度
- 旧裁剪滑块不再独立占空间
- 全局工作区使用时间轴 overlay 表达
- 精确数值只作为辅助显示

## 11.5 风险：用户混淆单 CSV 裁剪和全局工作区

处理：

- 文案明确：
  - 拖 CSV 左右边缘：裁剪该 CSV
  - 拖顶部工作区边界：裁剪最终预览 / 导出范围
- README 中配图或示意说明

---

## 12. 首版验收标准

满足以下条件时，可视为本次完整 UI 重构和功能实现完成：

1. UI 稳定呈现四区布局
2. 模块 1 只负责文件管理和文件级数值状态
3. 模块 2 只负责图表预览
4. 模块 3 只负责参数筛选、参数颜色和图表样式
5. 模块 4 统一承载所有时间编辑
6. CSV clip 主体拖动可以修改 offset 并实时预览
7. CSV clip 左右边缘伸缩可以裁剪单个 CSV 有效片段并实时预览
8. 全局工作区边界可以确定最终预览 / 导出时间范围
9. 设为基准后相对布局不跳变
10. 多选文件可整体平移
11. 导出 PNG 与模块 2 预览一致
12. 核心测试、GUI 测试和 README 均已更新

---

## 13. 后续 Session 执行建议

后续 Session 建议按以下顺序执行：

1. **Session A：四区骨架**
   - 只重构布局容器和模块拆分
   - 保证当前主流程不坏

2. **Session B：模块迁移**
   - 完成模块 1 / 2 / 3 的控件迁移
   - 清理原左侧大滚动区职责

3. **Session C：统一时间轴基础**
   - 在模块 4 画出 clip、刻度和全局工作区
   - 接入旧 `visible_range_seconds`

4. **Session D：文件对齐拖动**
   - 实现 clip 主体拖动、多选平移、吸附、预览节流

5. **Session E：clip 伸缩裁剪**
   - 增加 per-session trim 数据模型和渲染支持
   - 实现左右边缘伸缩

6. **Session F：测试、README、回归**
   - 完成文档和测试
   - 用实际 CSV 手工验证导出结果

---

## 14. 当前结论

后续开发应以本文件为唯一 plan：

- 不再单独执行“只加 offset 时间轴”的旧计划
- 不再保留“独立裁剪滑块 + 独立对齐时间轴”的旧设计
- 模块 4 采用统一 PR 式时间轴
- CSV clip 主体用于对齐
- CSV clip 左右边缘用于单文件裁剪
- 顶部或覆盖式全局工作区用于最终预览 / 导出裁剪
- 所有时间编辑都应利用实时预览形成快速反馈闭环
