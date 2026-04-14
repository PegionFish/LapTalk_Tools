# `csv_visual` 峰谷识别、赋值与派生曲线实施方案

## 1. 背景

`csv_visual` 当前已经具备以下能力：

- 基于 `LoadedCsvSession` 维护多 CSV 会话
- 通过 `offset_seconds` + 时间轴拖拽完成多文件对齐
- 通过 `source_trim_start_seconds / source_trim_end_seconds` 裁剪单文件有效片段
- 通过 `SeriesKey(session_id, column_index)` 为不同文件中的同名参数独立配色
- 通过 `build_comparison_figure(...)` 将多文件参数叠加绘图并自动预览

当前缺失的是“事件层”能力：

- 还不能自动识别曲线中的高峰 / 低谷
- 还不能把这些峰谷映射为“用户定义的业务数值”
- 还不能把映射后的数值串联成新的派生曲线
- 还不能在多文件对齐后，把“同一组峰谷事件”分别赋给不同文件不同数值
- 还不能对峰谷标记点做逐点颜色管理

本方案的目标是在**不破坏现有多文件对齐与预览工作流**的前提下，为 `csv_visual` 增加一层“峰谷事件分析 + 数值映射 + 派生曲线渲染”能力。

---

## 2. 需求拆解

用户提出的目标可以拆为 5 个独立但串联的能力：

1. **自动检测**
   - 基于 CSV 曲线自动判断高峰 / 低谷

2. **人工赋值**
   - 允许用户给每组峰谷事件填写一个数值

3. **派生曲线**
   - 将这些数值按时间顺序连接为一条新的曲线

4. **多文件独立赋值**
   - 当多个 CSV 已对齐时，同一组对齐后的峰谷事件允许给不同文件填写不同值

5. **逐点颜色**
   - 不同文件的峰谷点颜色可不同
   - 颜色要支持按“单个峰谷点”而不是只按“整条曲线”修改

---

## 3. 首版范围与边界

## 3.1 首版明确纳入范围

- 基于单个“检测源参数”自动识别峰 / 谷
- 单文件、多文件两种模式都支持
- 多文件模式下基于**对齐后的时间轴**分组峰谷事件
- 每个分组可按文件分别赋值
- 每个峰谷点可按文件逐点改颜色
- 将赋值结果渲染为每个文件一条派生曲线
- README、单元测试、GUI 测试同步更新

## 3.2 首版建议限制

为控制复杂度，首版建议采用以下限制：

- **一次只配置 1 组检测源参数**
  - 即用户先指定一个“峰谷检测基线参数”
  - 该参数在多文件模式下自动扩展为多条 `SeriesKey`
- **不做手工合并 / 拆分峰谷组**
  - 分组完全由“对齐后的时间容差”自动生成
- **不做配置持久化**
  - 峰谷赋值、颜色调整仅保留在当前会话内存中
- **不引入新依赖**
  - 峰谷检测逻辑用纯 Python 写在 `core.py`
  - 不新增 `scipy` 等依赖

## 3.3 首版暂不纳入范围

- 自动推荐最佳峰值检测阈值
- 人工拖拽峰谷点改组
- 在预览图上直接点选并编辑峰谷点
- 多个检测源参数同时生成多套峰谷映射
- 峰谷赋值导入 / 导出为独立配置文件

## 3.4 依赖与许可策略

用户已明确：

- **允许新增依赖或外部库**
- 但这些依赖 / 外部库必须能与 **GPLv3** 兼容

因此后续实现建议采用以下筛选原则：

1. **优先零新增依赖**
   - 若纯 Python 即可满足首版精度和可维护性，优先保持无新依赖

2. **若引入依赖，只选许可证明确且常见的宽松许可**
   - 例如 modified BSD / BSD-3-Clause / MIT / PSF / Apache-2.0
   - 这些通常可与 GPLv3 组合使用

3. **拒绝许可证不清晰或需要额外法律判断的库**
   - 无 SPDX / 无官方 LICENSE / 仅 README 口头说明 / 多重许可证不清晰

4. **实现前按具体版本再次核验**
   - 不能只按库名判断，必须核对目标版本发布页或官方文档

基于当前公开资料，以下候选路线可用作首版优先级：

- **首选路线 A：直接引入 `scipy`**
  - 使用 `scipy.signal.find_peaks`
  - 谷值检测可通过对 `y_values` 取负后再次调用 `find_peaks`
  - `SciPy` 官方页面标注为 BSD 许可，适合作为 GPLv3 项目的兼容候选
  - 通常还会配合 `numpy`，而 `NumPy` 官方文档标注为 modified BSD
  - 在“不修改依赖源码，只作为库导入使用”的前提下，这条路线工程风险最低、实现速度最快

- **备选路线 B：不新增峰值库**
  - 在 `core.py` 自实现峰谷检测纯函数
  - 仅在 `scipy` 方案遇到实际接入问题时启用

- **可选路线 C：引入 `PeakUtils`**
  - PyPI 页面标注为 MIT
  - 但它仍依赖 `numpy` / `scipy`，增益可能小于直接使用 `scipy.signal.find_peaks`
  - 因此只作为次选，不作为首推

本方案当前的推荐顺序调整为：

1. 默认采用 **`scipy.signal.find_peaks`**
2. 峰值与谷值统一走 `SciPy`，通过参数和对负序列取峰完成
3. 只有在 `scipy` 接入或分发上出现实际问题时，再退回纯 Python 方案
4. 除非有明显收益，否则不建议再叠加额外峰值包装库

后续进入开发阶段时，还应同步处理以下事项：

- 在实现 session 中更新 `csv_visual/requirements.txt`
- 由用户手动执行 `pip install -r requirements.txt`
- 在 README 中明确新增的 `scipy` 依赖与用途

---

## 4. 关键设计决策

## 4.1 “峰谷事件”必须独立于原始传感器曲线

建议把峰谷识别视为一层新数据，而不是直接混进 `VisibleSeries`：

- 原始曲线：用于显示真实 CSV 数值
- 峰谷点：用于标记检测出的局部极值
- 派生曲线：用于显示用户赋值后的新序列

这样可以避免把“源数据值”和“业务映射值”混在一个概念中。

## 4.2 多文件分组必须基于“对齐后的时间轴”

在多文件模式下，峰谷组的建立必须基于：

```text
aligned_seconds = raw_elapsed_seconds + offset_seconds
```

原因：

- 用户已经通过时间轴把不同 CSV 拖到同一时间参考系
- 如果仍按原始时间轴分组，多文件之间无法形成“对应事件”

## 4.3 派生曲线建议使用副 Y 轴

用户赋的值通常不是原始传感器单位，例如：

- 原曲线是功耗 `W`
- 用户赋值可能是 1~10 的评分

若把派生曲线画在同一 Y 轴上，极易产生比例失真。因此建议首版默认：

- 原始参数继续使用主 Y 轴
- 派生曲线使用右侧副 Y 轴
- 副轴标签文案固定为“赋值曲线”

这是首版最稳妥的渲染方案。

## 4.4 峰谷点颜色采用“逐点覆盖，按系列回退”

建议颜色回退链如下：

```text
峰谷点显式颜色
→ 对应原始 SeriesKey 的曲线颜色
→ 系统默认调色板
```

这样既满足“一对一修改”，也不会让未编辑点全部变成同色黑点。

---

## 5. 数据模型设计

## 5.1 建议新增 `ExtremaDetectionConfig`

建议在 `csv_visual/hwinfo_plotter/core.py` 中新增：

```python
@dataclass(frozen=True)
class ExtremaDetectionConfig:
    enabled: bool = False
    source_series_keys: tuple[SeriesKey, ...] = ()
    mode: str = "both"  # "peak" | "valley" | "both"
    min_distance_seconds: float = 1.0
    min_prominence: float = 0.0
    smoothing_window: int = 1
    alignment_tolerance_seconds: float = 1.0
    use_secondary_axis: bool = True
```

说明：

- `source_series_keys`
  - 实际参与检测的源曲线
  - 单文件时为 1 条，多文件时为多条
- `mode`
  - 允许只检测峰、只检测谷、或同时检测
- `min_distance_seconds`
  - 控制相邻局部极值最小间距，避免毛刺过密
- `min_prominence`
  - 控制突出度阈值，过滤噪声
- `smoothing_window`
  - 可选平滑窗口，`1` 表示不平滑
- `alignment_tolerance_seconds`
  - 多文件分组时，两个峰谷落在多大时间窗内视为同组

## 5.2 建议新增 `DetectedExtremum`

```python
@dataclass(frozen=True)
class DetectedExtremum:
    event_id: str
    key: SeriesKey
    kind: str  # "peak" | "valley"
    source_seconds: float
    aligned_seconds: float
    source_value: float
    prominence: float
    sample_index: int
```

用途：

- 表示“某文件某参数的一次极值事件”
- 同时保留原始时间和对齐后时间

## 5.3 建议新增 `AlignedExtremaGroup`

```python
@dataclass(frozen=True)
class AlignedExtremaGroup:
    group_id: str
    kind: str
    anchor_seconds: float
    members: tuple[DetectedExtremum, ...]
```

说明：

- `kind` 只能与同类型极值分组
  - 峰只与峰分组
  - 谷只与谷分组
- `anchor_seconds`
  - 作为该组派生曲线的统一 X 坐标

## 5.4 建议新增 `ExtremaPointKey`

```python
@dataclass(frozen=True)
class ExtremaPointKey:
    group_id: str
    key: SeriesKey
```

用途：

- 唯一标识“某文件在某组峰谷中的那个点”
- 用作赋值和颜色修改的主键

## 5.5 建议新增 `ExtremaAssignment`

```python
@dataclass(frozen=True)
class ExtremaAssignment:
    point_key: ExtremaPointKey
    assigned_value: float | None = None
```

说明：

- `None` 表示该点尚未赋值
- 派生曲线只连接有值的点

## 5.6 建议新增颜色映射

建议在 GUI 状态中新增：

```python
self.extrema_point_colors: dict[ExtremaPointKey, str] = {}
```

而不是复用现有：

```python
self.series_colors: dict[SeriesKey, str]
```

原因：

- `series_colors` 是按整条原曲线着色
- 用户需求是按“单个峰谷点”逐点修改颜色

---

## 6. 检测算法方案

## 6.1 设计目标

在默认引入 `scipy` 的前提下，首版算法要满足：

- 对平滑曲线能正确找出主峰 / 主谷
- 对轻微噪声有一定容忍度
- 行为可解释，可通过参数调节
- 纯函数可测试

## 6.2 推荐算法流程

建议流程如下：

1. 读取源序列 `x_values / y_values`
2. 先应用现有 `source_trim_*` 过滤，忽略被裁掉的片段
3. 选配轻量平滑
   - `smoothing_window = 1` 时跳过
   - `>1` 时可选 `numpy` 卷积或轻量滑动平均
4. 峰值检测
   - 调用 `scipy.signal.find_peaks(y_values, ...)`
5. 谷值检测
   - 调用 `scipy.signal.find_peaks(-y_values, ...)`
6. 从 `find_peaks` 返回结果中读取：
   - `prominences`
   - `left_bases / right_bases`
   - 其他需要的属性
7. 转换为 `DetectedExtremum`
8. 再按本方案的多文件分组规则生成 `AlignedExtremaGroup`

## 6.3 为什么首版改为优先使用 `scipy.signal.find_peaks`

用户已明确允许：

- 新增依赖
- 只要与 GPLv3 兼容即可

在此前提下，`scipy.signal.find_peaks` 的优势非常直接：

- 已经覆盖峰值检测的主流程
- 对 prominence、distance、plateau 等场景支持更成熟
- 比纯 Python 自实现更省开发和维护成本
- 后续更容易调参和解释行为

因此本方案不再把“纯 Python 自实现”作为默认路线，而是改为：

- **默认使用 `SciPy`**
- **纯 Python 作为后备预案**

## 6.4 多文件分组算法

在每个文件的源序列都得到 `DetectedExtremum` 后，执行统一分组：

1. 先按 `kind` 分开处理
2. 按 `aligned_seconds` 升序排序
3. 遍历构建组：
   - 若当前事件与当前组的 `anchor_seconds` 之差 `<= alignment_tolerance_seconds`
   - 则并入同组
   - 否则新开一组
4. 对组内同一 `SeriesKey` 若出现多个候选：
   - 保留与组锚点最近者
   - 若同距，峰取更高 prominence，谷取更高 prominence

## 6.5 组锚点选取规则

建议规则：

- 若当前组内包含“基准文件”事件，则取其 `aligned_seconds` 作为 `anchor_seconds`
- 否则取所有成员 `aligned_seconds` 的中位数

理由：

- 多文件对齐语义本来就是围绕基准文件建立
- 没有基准成员时用中位数更稳健

这是一条**推荐决策**，不是唯一可能方案。

---

## 7. 派生曲线构建方案

## 7.1 派生点来源

对每个 `AlignedExtremaGroup`：

- 遍历组内成员
- 为每个成员构造 `ExtremaPointKey(group_id, series_key)`
- 查找该点是否存在用户赋值

若存在：

- X 坐标取 `group.anchor_seconds`
- Y 坐标取 `assigned_value`

## 7.2 派生曲线组织方式

建议按文件分别生成派生曲线，即：

- `RunA · 赋值曲线`
- `RunB · 赋值曲线`

这与用户需求第 4 条完全一致：

- 同一组对齐后的峰谷，可以给两个文件赋不同值
- 这些值最终应形成两条不同的派生曲线

## 7.3 缺失赋值的处理

建议规则：

- 未赋值点不参与派生曲线
- 若中间存在未赋值点，则该文件派生曲线在该处断开

不要默认补零，否则会误导曲线意义。

## 7.4 峰与谷的连接策略

首版建议：

- 峰和谷共用一套时间序列
- `kind` 只影响检测与分组，不影响连接顺序
- 所有已赋值点按 `anchor_seconds` 排序后连接

如果后续用户觉得“峰/谷混连”可读性差，再扩展为：

- 峰值派生曲线
- 谷值派生曲线

但首版先保持一套派生曲线，复杂度最低。

---

## 8. 渲染方案

## 8.1 推荐改造点

渲染入口建议仍集中在 `csv_visual/hwinfo_plotter/core.py` 的多文件渲染层：

- 保持 `filter_visible_series(...)` 负责原始曲线
- 新增峰谷分析与派生曲线构建函数
- 再由 `build_comparison_figure(...)` 统一渲染

## 8.2 建议新增纯函数

建议新增：

```python
def detect_series_extrema(...)
def detect_extrema_for_sessions(...)
def group_aligned_extrema(...)
def build_assigned_curve_points(...)
```

## 8.3 图层顺序建议

建议渲染顺序：

1. 原始传感器曲线
2. 原曲线上的峰谷散点标记
3. 副 Y 轴上的派生赋值曲线
4. 派生曲线上的赋值点标记

这样用户能同时看到：

- 原始峰谷落点
- 赋值后形成的新曲线

## 8.4 样式建议

建议默认样式：

- 原曲线：现有实线
- 峰谷点：较醒目的圆点
- 派生曲线：与原文件同色系的虚线
- 派生点：颜色与峰谷点保持一致

## 8.5 图例建议

图例文案建议区分原始曲线和派生曲线：

- `RunA · [002] CPU`
- `RunA · 赋值曲线`
- `RunB · [002] CPU`
- `RunB · 赋值曲线`

若图例过长，后续可再加“隐藏峰谷标记图例”的优化开关。

---

## 9. GUI 方案

## 9.1 不建议做预览图内直接编辑

当前预览是后台生成 PNG 再显示到 `ttk.Label`，不是内嵌交互式 Matplotlib 画布。

因此首版不建议：

- 在预览图片上点击峰谷点直接编辑

原因：

- 命中测试复杂
- 坐标映射和缩放适配成本高
- 会显著拉大 GUI 改造范围

## 9.2 推荐新增“峰谷映射”子模块

建议在 `参数与图表设置` 的滚动区域中新增一个 `Labelframe`：

- 标题：`峰谷映射`

内部控件建议包括：

1. 开关
   - `启用峰谷映射`

2. 检测源参数
   - 只允许从当前共享参数中选择 1 项

3. 检测参数
   - 模式：峰 / 谷 / 峰+谷
   - 最小间隔（秒）
   - 最小突出度
   - 平滑窗口
   - 分组容差（秒）

4. 操作按钮
   - `重新检测`
   - `清空赋值`
   - `清空峰谷点颜色`

5. 峰谷组表格
   - 展示每组事件并允许编辑赋值与颜色

## 9.3 峰谷组表格结构

建议使用 `ttk.Treeview` 或“左表 + 右编辑器”组合。

推荐表格列：

- `组号`
- `类型`
- `对齐时间`
- `文件`
- `原始峰值`
- `赋值`
- `颜色`

其中：

- 一组峰谷对应多行
- 每行代表某文件在该组中的一个点

这样能自然表达“同组、不同文件、不同赋值”的需求。

## 9.4 赋值编辑方式

首版建议：

- 选中表格行后，在下方使用单独编辑区修改

编辑区字段：

- 当前组号（只读）
- 当前文件（只读）
- 原始峰值（只读）
- 赋值输入框
- 颜色输入框
- `应用赋值`
- `应用颜色`
- `清除赋值`
- `清除颜色`

原因：

- Tk 的 `Treeview` 原生不擅长复杂单元格编辑
- 下方编辑器实现更稳、更容易测试

## 9.5 与现有颜色系统的关系

现有 `参数颜色` 区继续负责：

- 原始曲线颜色

新增 `峰谷映射` 区负责：

- 峰谷标记点颜色
- 派生点颜色

不要把二者混到一个列表里，否则用户很难理解“改的是整条曲线还是单个峰谷点”。

## 9.6 刷新时机

以下操作应触发峰谷重算或重绘：

- 切换检测源参数
- 修改检测参数
- 修改 `offset_seconds`
- 修改 `source_trim_*`
- 添加 / 移除文件
- 切换基准文件

以下操作只触发重绘，不必重算：

- 修改赋值
- 修改峰谷点颜色
- 修改图表样式

---

## 10. 与现有架构的接入点

## 10.1 `csv_visual/hwinfo_plotter/core.py`

建议承担：

- 峰谷检测纯函数
- 多文件分组纯函数
- 派生曲线点构建纯函数
- 渲染副 Y 轴与峰谷散点

## 10.2 `csv_visual/hwinfo_plotter/gui.py`

建议承担：

- 新的峰谷映射 GUI 状态
- 峰谷设置区和编辑区
- 重新检测按钮
- 峰谷赋值 / 颜色编辑
- 将峰谷相关配置打包进预览请求

## 10.3 `PreviewRenderRequest`

建议扩展 `PreviewRenderRequest`，新增：

```python
extrema_config: ExtremaDetectionConfig | None = None
extrema_assignments: dict[ExtremaPointKey, float] = field(default_factory=dict)
extrema_point_colors: dict[ExtremaPointKey, str] = field(default_factory=dict)
```

这样：

- 后台预览线程拿到的是一份完整快照
- 避免后台线程再去读 GUI 可变状态

## 10.4 `csv_visual/hwinfo_plotter/__init__.py`

若新增 dataclass / helper 对外可见，需要同步导出，保持测试导入路径一致。

---

## 11. 分阶段实施计划

## Phase 1：核心数据结构与检测纯函数

目标：

- 先把“可计算、可测试”的底层打稳

任务：

1. 新增峰谷配置、事件、分组、点键模型
2. 基于 `scipy.signal.find_peaks` 实现单序列峰谷检测
3. 实现多文件峰谷分组
4. 实现派生曲线点构建
5. 为上述逻辑补充单元测试

验收：

- 不改 GUI 的情况下，`test_core.py` 能独立验证：
  - 峰 / 谷检测
  - 分组
  - 派生点排序
  - 缺失赋值断开逻辑

## Phase 2：预览渲染与副轴支持

目标：

- 在现有图表上把峰谷标记与派生曲线画出来

任务：

1. 扩展 `PreviewRenderRequest`
2. 扩展 `build_comparison_figure(...)`
3. 新增峰谷散点渲染
4. 新增副 Y 轴派生曲线渲染
5. 处理图例与颜色回退

验收：

- 单元测试可验证：
  - 峰谷点被绘制
  - 派生曲线走副 Y 轴
  - 多文件不同赋值形成不同派生曲线
  - 点级颜色不会与 `SeriesKey` 颜色冲突

## Phase 3：GUI 峰谷映射编辑器

目标：

- 让用户可视化查看分组并编辑赋值 / 颜色

任务：

1. 新增 `峰谷映射` 模块
2. 新增检测源参数选择
3. 新增检测参数编辑
4. 新增峰谷组列表
5. 新增赋值与颜色编辑器
6. 打通自动预览刷新

验收：

- GUI 测试可验证：
  - 重新检测后列表刷新
  - 同组不同文件可填写不同值
  - 单点颜色修改正确进入预览请求

## Phase 4：文档、回归与手工验证

目标：

- 收尾并形成可交付状态

任务：

1. 更新 `csv_visual/README.md`
2. 补充 `test_core.py`
3. 补充 `test_gui.py`
4. 用样例 CSV 完成手工验证

验收：

- README 覆盖新工作流
- 自动测试通过
- 手工验证满足使用场景

---

## 12. 测试计划

## 12.1 建议新增 `csv_visual/tests/test_core.py` 场景

1. 单序列可识别明显峰值
2. 单序列可识别明显谷值
3. `min_distance_seconds` 能压制过密极值
4. `min_prominence` 能过滤噪声极值
5. 对齐后的两个文件可被分到同一组
6. 峰与谷不会误合并到同一组
7. 同组可对两个文件赋不同值
8. 派生点按 `anchor_seconds` 正确排序
9. 未赋值点不会被自动补零
10. 点级颜色覆盖优先级高于 `series_colors`

## 12.2 建议新增 `csv_visual/tests/test_gui.py` 场景

1. 启用峰谷映射后可选择检测源参数
2. 修改检测参数会触发重新检测
3. 选中峰谷组行后可应用赋值
4. 选中峰谷组行后可应用颜色
5. 多文件同组可保存不同赋值
6. `build_preview_request()` 会带上峰谷配置、赋值与颜色
7. 修改 `offset_seconds` 后会刷新分组结果
8. 修改 `source_trim_*` 后会刷新分组结果

## 12.3 手工验证建议

建议使用 2 个已对齐或可对齐的 CSV，手工走通：

1. 选择一个共享参数作为检测源
2. 重新检测峰谷
3. 检查峰谷点是否落在可理解的位置
4. 对同一组中的两个文件填写不同赋值
5. 修改某个峰谷点颜色
6. 观察预览中：
   - 原始曲线上有峰谷点
   - 副轴上有派生曲线
   - 两个文件派生曲线不同
7. 导出 PNG 并核对结果

---

## 13. 风险与处理策略

## 13.1 风险：噪声导致峰谷过多

处理：

- 首版必须暴露 `min_distance_seconds`
- 首版必须暴露 `min_prominence`
- 必要时加 `smoothing_window`

## 13.2 风险：多文件自动分组不符合用户预期

处理：

- 首版用“分组容差秒数”公开可调
- 文档明确说明当前是自动分组，不支持手工 merge / split
- 后续若需求稳定，再考虑手工调整分组

## 13.3 风险：派生曲线与原曲线量纲冲突

处理：

- 默认走副 Y 轴
- 文档明确“赋值曲线不代表原始单位”

## 13.4 风险：GUI 复杂度上涨

处理：

- 不做图内交互编辑
- 使用列表 + 下方编辑器的保守方案
- 先打通数据流，再考虑更高级交互

## 13.5 风险：同一文件中检测到多个近邻峰值，导致组内重复

处理：

- 组内同 `SeriesKey` 只保留一个成员
- 保留“离锚点更近 / prominence 更高”的点

---

## 14. 待确认事项

以下内容本次先按推荐决策规划，后续实现前建议由用户确认：

- TODO（用户确认）：
  - 首版是否只针对 **1 个检测源参数**，还是要同时支持多个检测源参数并行映射

- TODO（用户确认）：
  - 派生曲线是否接受“峰与谷混合连接”为默认行为；若不接受，首版需拆成“峰值曲线 / 谷值曲线”两套

- TODO（用户确认）：
  - 组锚点时间是否按“基准文件优先，否则取中位数”执行

- TODO（用户确认）：
  - 点颜色编辑是否只需要“逐点修改”，还是还要加“按文件一键套色”的批量入口

---

## 15. 后续 Session 推荐顺序

建议后续开发按以下顺序推进：

1. **Session A：核心检测纯函数**
   - 只改 `core.py` 与 `test_core.py`
   - 不碰 GUI

2. **Session B：渲染与预览请求**
   - 打通峰谷点和派生曲线的图层

3. **Session C：GUI 编辑器**
   - 打通列表、赋值、颜色、重新检测

4. **Session D：README、GUI 测试、手工回归**
   - 完成可交付收尾

---

## 16. 当前结论

**可以开发，而且适合建立在当前 `csv_visual` 现有多文件对齐架构上。**

推荐采用以下首版路线：

1. 以现有 `SeriesKey` / `LoadedCsvSession` / 时间轴对齐模型为基础
2. 峰谷检测默认直接采用 `scipy.signal.find_peaks`
3. 新增“峰谷事件层”与“派生赋值层”，不要污染原始曲线数据结构
4. 峰谷检测与分组优先做成纯函数，保证可测
5. 派生曲线默认走副 Y 轴，避免量纲冲突
6. GUI 首版使用“列表 + 编辑区”，不做预览图内直接点选

如果后续 session 无新增范围变更，建议按本方案直接进入实现阶段。
