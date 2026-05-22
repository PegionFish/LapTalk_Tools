# Frontend2Video 开发规格文档

- Status: draft
- Date: 2026-05-21
- Intended Repo: `https://github.com/PegionFish/frontend2video.git`
- Purpose: 这是一份可独立打开、可直接执行的开发基线文档。后续 session 即使不再访问当前仓库，也应能仅凭本文启动并推进开发。
- Scope: 定义独立桌面应用的产品目标、继承的旧链路行为、推荐技术栈、模块边界、关键算法、默认值、平台策略、阶段计划与验收标准。本轮不实现应用代码。

## 1. 文档定位

这份文档的目标不是记录“想法”，而是为后续在独立仓库中的开发提供一份足够自包含的实现基线。

读者打开本文后，应该能直接知道：

- 应用要解决什么问题
- 旧原型的行为细节是什么
- 新应用必须保留哪些渲染语义
- 推荐的桌面技术路线是什么
- 代码应该如何组织
- 关键模块之间如何通信
- FFmpeg 和 Chromium 应如何接入
- 哪些功能必须先做，哪些可以延后

本文不要求读者再回头阅读其他仓库中的 PowerShell 脚本，除非要做历史比对。

## 2. 产品目标

构建一个名为 `Frontend2Video` 的独立桌面应用，用于把一个或多个本地 HTML 页面渲染成视频文件，优先满足 LapTalk 现有“HTML 可视化页面导出为透明 MOV”的工作流。

必须满足的用户目标：

- 用户可以通过拖放导入单个或多个 HTML 页面
- 用户也可以通过系统文件选择器多选导入 HTML 页面
- 用户可以在 GUI 中调整输出分辨率和帧率
- 若页面没有嵌入时长 metadata，系统统一渲染 `30s`
- 窗口右侧实时显示当前渲染画面
- 当前画面下方显示渲染进度
- 进度下方显示导出文件夹
- 底部提供四个按钮：`导出`、`停止`、`打开导出文件夹`、`设置`
- 功能按钮左侧显示应用版本和 FFmpeg 版本
- 设置中至少要能配置 FFmpeg 可执行文件路径，并显示其版本
- 启动时会自动检查 FFmpeg；找不到时允许手动选择
- 应用必须兼容 Windows、Linux、macOS
- 应用必须自包含 Chromium，不依赖系统安装的 Chrome 或 Edge
- 在支持的 macOS 设备上，尽可能启用硬件加速导出；若当前输出规格不适合安全启用，则自动回退软件编码

## 3. 非目标

第一阶段不做以下内容：

- 不做 HTML 编辑器
- 不做视频剪辑时间线
- 不做云端渲染或远程任务队列
- 不做联网账号、自动更新平台或授权系统
- 不做 React/Vue/Svelte 前端框架引入，除非后续维护成本证明值得
- 不承诺第一阶段就支持所有编码格式
- 不承诺第一阶段就把 FFmpeg 一起打进安装包；可以先通过探测和设置路径跑通

## 4. 旧原型行为快照

这一节是本文最重要的自包含信息。它把旧原型的关键渲染语义直接写在这里，后续新仓库应继承这些行为，而不是靠“看旧脚本猜”。

### 4.1 旧原型的核心思路

旧原型是一条“本地 HTML 页面 -> Chromium 抓帧 -> PNG 帧序列 -> FFmpeg 编码视频”的离线链路。

其本质流程如下：

1. 启动一个 Chromium headless 会话
2. 打开本地 HTML 页面
3. 逐帧推进页面时间
4. 每帧截图为 PNG
5. 把所有 PNG 交给 FFmpeg 编码
6. 输出 `MOV` 或 `MP4`
7. 清理临时浏览器 profile 和中间文件

### 4.2 旧原型的默认值

后续新应用如无明确设计理由，不应随意改变这些默认值：

- 默认宽度：`3840`
- 默认高度：`2160`
- 默认帧率：`60`
- 默认时长：`30s`
- 默认 H.264 `crf`：`18`
- 默认页面稳定等待 `settle_ms`：`120`
- 默认页面虚拟加载预算 `virtual_time_budget_ms`：`1500`
- 默认输出格式：`MOV`
- 默认 MOV 编码策略：`ProRes 4444`
- 默认 MOV 像素格式：`yuva444p10le`

### 4.3 时长解析规则

时长解析顺序必须保持一致：

1. 用户手动指定的时长
2. HTML 中的 `<meta name="laptalk:duration-seconds" content="...">`
3. 默认 `30s`

建议后续实现直接采用如下逻辑：

```js
async function resolveDurationSeconds({ htmlPath, manualDurationSeconds }) {
  if (Number.isFinite(manualDurationSeconds) && manualDurationSeconds > 0) {
    return { seconds: manualDurationSeconds, source: "manual" };
  }

  const html = await fs.promises.readFile(htmlPath, "utf8");
  const match = html.match(
    /<meta\b[^>]*\bname\s*=\s*["']laptalk:duration-seconds["'][^>]*\bcontent\s*=\s*["'](\d+(?:\.\d+)?)["'][^>]*>/i
  );

  if (match) {
    const value = Number(match[1]);
    if (Number.isFinite(value) && value > 0) {
      return { seconds: value, source: "html-meta" };
    }
  }

  return { seconds: 30, source: "default-30s" };
}
```

### 4.4 帧数与实际时长的换算规则

旧原型不是按“任意浮点时长”直接编码，而是先把时长换算为帧数。

建议保留这一逻辑：

```js
const totalFrames = Math.max(1, Math.round(durationSeconds * fps));
const frameAlignedDurationSeconds = totalFrames / fps;
```

这意味着：

- 若 `durationSeconds * fps` 不是整数，实际编码时长会对齐到最近整帧
- UI 可以显示“声明时长”和“帧对齐后时长”，但至少内部要用对齐后的帧数驱动渲染

### 4.5 页面时间推进规则

旧原型优先调用页面暴露的统一钩子：

```js
window.__setRenderTime = async (ms) => {
  // 页面根据 ms 进入对应的渲染状态
};
```

如果页面没有这个钩子，就退回到通用动画冻结逻辑：

- `document.getAnimations()`
- 对每个 animation 执行 `pause()`
- 将 `currentTime` 设置为目标时间

这是新应用必须保留的兼容策略。

### 4.6 页面渲染模式注入规则

旧原型在抓帧前会向页面注入渲染模式信息。新应用建议保留：

- 给 `document.documentElement` 设置属性 `data-render-mode="1"`
- 给 `document.documentElement.style` 设置 CSS 变量 `--render-ms`

可选地，为兼容一些已有页面，也可以在加载页面时保留以下 query 参数：

- `laptalkRender=1`
- `renderMs=<当前渲染毫秒>`
- `renderWidth=<宽度>`
- `renderHeight=<高度>`
- `renderSettleMs=<稳定等待毫秒>`

保留 query 参数不是必须，但如果未来要直接兼容已有 HTML 页面，建议保留。

### 4.7 页面准备脚本

旧原型的语义可抽象成如下脚本，后续可直接在 Electron 隐藏窗口内执行：

```js
async function preparePageForRender(renderTimeMs, settleMs) {
  if (document.fonts && document.fonts.ready) {
    try {
      await document.fonts.ready;
    } catch {}
  }

  document.documentElement.setAttribute("data-render-mode", "1");
  document.documentElement.style.setProperty("--render-ms", String(renderTimeMs));

  if (typeof window.__setRenderTime === "function") {
    await window.__setRenderTime(renderTimeMs);
  } else if (document.getAnimations) {
    for (const animation of document.getAnimations()) {
      try {
        animation.pause();
        animation.currentTime = renderTimeMs;
      } catch {}
    }
  }

  await new Promise((resolve) =>
    requestAnimationFrame(() => requestAnimationFrame(resolve))
  );

  if (settleMs > 0) {
    await new Promise((resolve) => setTimeout(resolve, settleMs));
  }
}
```

### 4.8 透明 MOV 的编码规则

旧原型对透明 MOV 的编码策略是：

```bash
ffmpeg -y \
  -framerate 60 \
  -i frame_%05d.png \
  -c:v prores_ks \
  -profile:v 4444 \
  -pix_fmt yuva444p10le \
  output.mov
```

新应用第一阶段应保持这一输出行为。

### 4.9 Opaque MP4 的历史兼容规则

虽然第一阶段 GUI 只要求 MOV，但历史上还存在 MP4 兼容路径：

```bash
ffmpeg -y \
  -framerate 60 \
  -i frame_%05d.png \
  -c:v libx264 \
  -pix_fmt yuv420p \
  -crf 18 \
  -movflags +faststart \
  output.mp4
```

这不是第一阶段必做功能，但后续可作为自然扩展点。

### 4.10 输出目录推断规则

旧原型的默认规则如下：

- 如果输入页面位于 `projects/<slug>/pages/`
  - 输出目录默认是 `projects/<slug>/exports/`
- 否则
  - 输出目录默认是输入页面同级的 `exports/`

建议独立仓库继续保留这一规则，因为它与现有 LapTalk 项目组织方式一致。

建议直接采用如下函数：

```js
function getDefaultOutputDirectory(pagePath) {
  const pageDirectory = path.dirname(pagePath);
  const parent = path.basename(pageDirectory).toLowerCase();

  if (parent === "pages") {
    return path.join(path.dirname(pageDirectory), "exports");
  }

  return path.join(pageDirectory, "exports");
}
```

### 4.11 临时文件组织

旧原型在每次渲染时都会创建一个临时根目录，里面至少包含：

- `browser-profile/`
- `captures/` 或 `frames/`

建议新应用也采用相同思路：

- 临时根目录命名：`laptalk-render-<uuid>`
- 抓帧目录：`frames/`
- 预览或缓存目录可选

这样可以把停止、失败清理、残留排查都控制在单一工作区内。

## 5. 新应用的固定技术路线

后续独立开发固定采用以下路线，不再在 session 中重复摇摆：

- 桌面容器：`Electron`
- UI：原生 `HTML / CSS / JavaScript`
- 主进程：Node.js
- 隐藏渲染器：Electron 自带 Chromium
- 视频编码：外部 `ffmpeg`
- 打包：`electron-builder`

选择这条路线的原因：

- Electron 打包后天然自带 Chromium，满足“应用自包含 Chromium”
- Windows、Linux、macOS 都有成熟分发路径
- 可以直接使用应用内 Chromium 渲染本地 `file://` HTML
- 不需要额外引入 Playwright/Puppeteer 去依赖系统浏览器
- 主界面和渲染工作器都能运行在同一技术栈中

明确不采用的路线：

- 不继续把旧 PowerShell 脚本扩展成 GUI 主体
- 第一阶段不采用 Tauri，因为其默认依赖系统 WebView，不符合“自包含 Chromium”目标

## 6. GUI 规格

应用主界面使用以下固定布局：

```text
+----------------------------------------------------------------------------------+
| LapTalk Render Studio                                         [导入 HTML] [清空] |
+--------------------------------------+-------------------------------------------+
| 左侧：任务队列                        | 右侧：当前预览                            |
|                                      |                                           |
| [a.html]  3840x2160 60fps 30s        |     当前选中任务的静态预览 / 渲染中帧      |
| [b.html]  1920x1080 30fps meta       |                                           |
| [c.html]  3840x2160 60fps 30s        |                                           |
|                                      |                                           |
+--------------------------------------+-------------------------------------------+
| 进度：███████████░░░░ 57%   1032 / 1800 帧                                      |
+----------------------------------------------------------------------------------+
| 导出文件夹： /path/to/exports                                            [浏览] |
+----------------------------------------------------------------------------------+
| v0.1.0 | ffmpeg 2026-... | [导出] [停止] [打开导出文件夹] [设置]                |
+----------------------------------------------------------------------------------+
```

界面约束：

- 左侧任务队列必须支持多任务
- 右侧预览区必须显示当前任务的真实渲染帧
- 预览区下方必须有明确的当前进度
- 进度下方必须显示输出目录
- 底部功能条右侧固定为四个按钮
- 底部功能条左侧固定显示应用版本和 FFmpeg 版本

建议的最小 UI 控件：

- 顶部：`导入 HTML`、`清空`
- 参数区：分辨率预设、自定义宽高、帧率选择
- 队列区：文件名、时长来源、状态
- 预览区：当前帧
- 进度区：任务名、百分比、当前帧、总帧数
- 导出目录区：目录文本、浏览按钮
- 底部：版本信息 + 4 个操作按钮

## 7. 功能需求

### 7.1 导入

- 支持拖放 `.html` 和 `.htm`
- 支持系统文件选择器多选导入
- 非 HTML 文件必须拒绝
- 同一绝对路径默认去重
- 去重时建议使用归一化绝对路径

### 7.2 队列

每个任务至少包含：

- `id`
- `pagePath`
- `outputDirectory`
- `outputFilename`
- `width`
- `height`
- `fps`
- `durationSeconds`
- `durationSource`
- `status`
- `progress`
- `error`

状态枚举：

- `idle`
- `ready`
- `rendering`
- `done`
- `stopped`
- `error`

### 7.3 参数

第一阶段参数要求：

- 输出格式固定为 `MOV`
- 分辨率预设：
  - `3840x2160`
  - `2560x1440`
  - `1920x1080`
- 支持自定义宽高
- 帧率预设：
  - `24`
  - `30`
  - `60`

### 7.4 实时预览

右侧显示的内容必须是“当前实际编码帧”，而不是另一个独立浏览器视图。

为避免预览吞掉性能，建议：

- 并非每一帧都推送到 UI
- 预览推送可节流到每 `6` 到 `10` 帧一次
- 或按时间节流到每 `100ms` 一次

预览保留策略：

- 渲染中显示最新推送帧
- 停止后保留最后一帧
- 错误后保留最后可用帧，并附错误信息

### 7.5 停止

点击 `停止` 时必须：

- 取消当前抓帧循环
- 杀掉当前 FFmpeg 子进程
- 关闭隐藏渲染窗口
- 删除当前任务临时目录
- 将任务状态设置为 `stopped`

停止不应清空整个队列，除非用户显式要求。

### 7.6 设置

第一阶段设置项固定如下：

- `ffmpegPath`
- `ffmpegVersion`
- `lastOutputDirectory`

设置文件建议放在：

- `app.getPath("userData")/settings.json`

## 8. 独立仓库建议目录

推荐在 `frontend2video` 仓库中采用如下结构：

```text
frontend2video/
  package.json
  electron-builder.yml
  app/
    main/
      main.js
      windows/
        main-window.js
        render-worker-window.js
      ipc/
        queue-ipc.js
        settings-ipc.js
        dialog-ipc.js
      services/
        app-state.js
        queue-service.js
        render-service.js
        settings-service.js
    preload/
      preload.js
    renderer/
      index.html
      styles/
        app.css
      scripts/
        app.js
        state.js
        queue-view.js
        preview-view.js
        settings-dialog.js
    core/
      duration.js
      output-paths.js
      ffmpeg.js
      ffmpeg-capabilities.js
      render-engine.js
      page-prepare.js
      temp-workspace.js
      validate-html.js
    assets/
      icons/
    docs/
      development-spec.md
      packaging.md
  vendor/
    ffmpeg/
      README.md
```

模块职责：

- `main/`
  - Electron 启动、窗口管理、IPC、系统能力调用
- `preload/`
  - 安全暴露可用 API
- `renderer/`
  - GUI
- `core/`
  - 与 UI 解耦的渲染和工具逻辑
- `vendor/ffmpeg/`
  - 可选放置随仓库管理的开发期 FFmpeg

## 9. 建议依赖与脚本

第一阶段尽量保持依赖极简。

建议运行时依赖：

- `electron`
- `uuid`

建议开发依赖：

- `electron-builder`

建议 `package.json` 脚本：

```json
{
  "scripts": {
    "dev": "electron .",
    "start": "electron .",
    "pack": "electron-builder --dir",
    "dist": "electron-builder"
  }
}
```

如果后续需要静态检查，再额外加入 `eslint`，但不是第一阶段强制项。

## 10. 渲染引擎设计

### 10.1 总体数据流

建议总数据流如下：

1. Renderer UI 导入 HTML 文件
2. Renderer 通过 preload API 把文件路径发送给主进程
3. 主进程构建任务对象并维护队列
4. 用户点击 `导出`
5. 主进程按顺序启动渲染任务
6. 渲染引擎创建隐藏渲染窗口
7. 逐帧抓图、写入临时目录、节流推送预览
8. 抓图完成后调用 FFmpeg 编码
9. 编码状态和结果回传 UI
10. 任务完成或失败后清理临时目录

### 10.2 隐藏渲染窗口

隐藏渲染窗口建议：

- `show: false`
- `useContentSize: true`
- `backgroundColor: "#00000000"`
- 固定为目标宽高
- `webPreferences.contextIsolation = true`
- `webPreferences.nodeIntegration = false`
- 如需兼容本地文件互访，可仅对该渲染窗口关闭 `webSecurity`

注意：

- `webSecurity: false` 只应用于隐藏渲染窗口
- 主 UI 窗口应继续保持默认更安全的配置

### 10.3 加载与等待策略

建议每个任务的页面加载流程：

1. 构造 `file://` URL
2. 可选附加兼容 query 参数
3. `loadURL()`
4. 等待 `did-finish-load`
5. 再等待一次页面准备脚本完成

建议保留页面超时控制：

- 页面加载等待超时：`6500ms`
- 页面稳定等待：`120ms`

这里的 `6500ms` 来自旧原型的 `1500ms + 5000ms` 等待思路。

### 10.4 抓帧循环

建议抓帧循环如下：

```js
for (let frameIndex = 0; frameIndex < totalFrames; frameIndex += 1) {
  const timeMs = (frameIndex * 1000) / fps;

  await runPrepareScript({
    renderTimeMs: timeMs,
    settleMs
  });

  const image = await hiddenWindow.webContents.capturePage({
    x: 0,
    y: 0,
    width,
    height
  });

  await writePngFrame(image, frameIndex);
  maybePublishPreview(image, frameIndex, totalFrames);
  publishProgress(frameIndex + 1, totalFrames);

  if (abortSignal.aborted) {
    throw new RenderStoppedError();
  }
}
```

注意事项：

- `capturePage()` 必须限制到目标区域
- 建议文件名使用 `frame_%05d.png`
- 实际写盘顺序要稳定，避免 FFmpeg 匹配失败

### 10.5 预览推送

建议预览图通过 IPC 从主进程发给 Renderer。

预览格式建议：

- `data:image/png;base64,...`
- 或 `nativeImage.toDataURL()`

建议消息结构：

```js
{
  taskId: "uuid",
  frameIndex: 123,
  totalFrames: 1800,
  previewDataUrl: "data:image/png;base64,..."
}
```

### 10.6 FFmpeg 编码

建议编码函数签名：

```js
async function encodeMovFromFrames({
  ffmpegPath,
  framesDirectory,
  fps,
  outputPath,
  signal
}) {}
```

第一阶段透明 MOV 的默认参数：

```js
[
  "-y",
  "-framerate", String(fps),
  "-i", path.join(framesDirectory, "frame_%05d.png"),
  "-c:v", "prores_ks",
  "-profile:v", "4444",
  "-pix_fmt", "yuva444p10le",
  outputPath
]
```

如果未来新增 MP4，再单独添加编码分支，不要污染第一阶段主流程。

### 10.7 停止与清理

停止逻辑需要同时打断三类对象：

- 抓帧循环
- FFmpeg 子进程
- 隐藏渲染窗口

建议清理顺序：

1. 标记 `abortController.abort()`
2. 杀死 FFmpeg 子进程
3. 销毁隐藏渲染窗口
4. 删除临时目录
5. 更新任务状态为 `stopped`

### 10.8 失败处理

常见失败分类：

- HTML 文件不存在
- HTML 文件扩展名不合法
- 页面加载超时
- 页面准备脚本执行失败
- 抓帧失败
- FFmpeg 不存在
- FFmpeg 编码失败
- 用户手动停止

建议所有错误统一转换为结构化对象返回给 UI：

```js
{
  code: "FFMPEG_NOT_FOUND",
  message: "Unable to locate FFmpeg executable.",
  details: ""
}
```

## 11. IPC 契约

为了让后续 session 少走弯路，这里直接定义建议的 IPC 面。

### 11.1 Renderer -> Main

- `dialog:import-html`
  - 打开系统文件选择器
- `queue:add-paths`
  - 加入多个 HTML 路径
- `queue:clear`
  - 清空队列
- `queue:update-defaults`
  - 更新默认分辨率、帧率
- `render:start`
  - 启动渲染
- `render:stop`
  - 停止当前渲染
- `settings:get`
  - 获取设置
- `settings:set-ffmpeg-path`
  - 手动设置 FFmpeg 路径
- `output:choose-directory`
  - 选择输出目录
- `output:open-directory`
  - 打开输出目录

### 11.2 Main -> Renderer 事件推送

- `queue:changed`
  - 推送整个队列或增量变化
- `render:progress`
  - 当前进度
- `render:preview`
  - 当前预览帧
- `render:status`
  - 当前任务状态变化
- `settings:changed`
  - 设置变更

### 11.3 进度消息结构

```js
{
  taskId: "uuid",
  fileName: "page.html",
  currentFrame: 1032,
  totalFrames: 1800,
  percent: 57.33,
  stage: "capturing"
}
```

编码阶段可改为：

```js
{
  taskId: "uuid",
  fileName: "page.html",
  currentFrame: 1800,
  totalFrames: 1800,
  percent: 100,
  stage: "encoding"
}
```

## 12. FFmpeg 接入策略

### 12.1 可执行文件探测顺序

后续新仓库应采用如下探测顺序：

1. 用户设置中保存的 `ffmpegPath`
2. 仓库或应用附带的 sidecar FFmpeg
3. 系统 `PATH`
4. 找不到时要求用户手动选择

建议 sidecar 搜索位置：

- 开发模式：
  - `vendor/ffmpeg/<platform>/ffmpeg(.exe)`
  - `tools/ffmpeg/<platform>/ffmpeg(.exe)`
- 打包模式：
  - `process.resourcesPath/bin/ffmpeg(.exe)`
  - `process.resourcesPath/ffmpeg/ffmpeg(.exe)`

### 12.2 FFmpeg 合法性校验

校验规则：

1. 文件存在
2. 可执行
3. 运行 `ffmpeg -version` 返回 `exitCode = 0`
4. 首行输出包含 `ffmpeg version`

版本解析建议：

```js
async function getFfmpegVersionLine(ffmpegPath) {
  const { stdout } = await execa(ffmpegPath, ["-version"]);
  return stdout.split(/\r?\n/)[0] ?? "";
}
```

如果不想引入 `execa`，直接用 Node `child_process.spawn` 也可以。

### 12.3 编码能力探测

为了支持 macOS 硬件加速策略，建议在启动时或第一次导出前探测：

```bash
ffmpeg -hide_banner -encoders
```

重点检查：

- `prores_ks`
- `libx264`
- `hevc_videotoolbox`
- `h264_videotoolbox`
- 可用时再检查是否存在稳定的 `prores_videotoolbox` 或同类能力

### 12.4 macOS 硬件加速策略

这里必须明确，避免未来 session 误解：

- 第一阶段主输出规格是透明 `MOV / ProRes 4444`
- 该规格默认仍以软件 `prores_ks` 为基线
- 不要为了“必须硬件加速”而强行替换透明 MOV 的稳定路径
- 正确策略是：
  - 启动时探测 macOS 上的硬件编码能力
  - 若未来新增不透明 `MP4/H.264/HEVC` 导出模式，则这些模式优先使用 `VideoToolbox`
  - 若运行环境中存在已验证可用、且符合当前目标规格的硬件 ProRes 路径，再在 `auto` 策略下启用
  - 否则自动回退 `prores_ks`

也就是说：

- “尽可能使用硬件加速”是能力探测后的策略
- “透明 MOV 交付稳定性”优先级高于“强行上硬件”

## 13. 页面契约

为了让未来页面作者知道如何兼容该渲染器，这里定义最小页面契约。

### 13.1 时长 metadata

页面若需要固定导出时长，可在 `<head>` 中写：

```html
<meta name="laptalk:duration-seconds" content="6.75">
```

### 13.2 时间控制钩子

页面若有多阶段 JavaScript 动画，应暴露：

```html
<script>
window.__setRenderTime = async (ms) => {
  // 根据 ms 切换页面状态
};
</script>
```

### 13.3 无钩子的纯动画页面

如果页面只是 CSS 动画或 Web Animations，渲染器会尝试自动冻结：

- `document.getAnimations()`
- `pause()`
- `currentTime = ms`

### 13.4 渲染模式样式

页面作者可选择读取：

- `data-render-mode="1"`
- CSS 变量 `--render-ms`

例如：

```css
html[data-render-mode="1"] .debug-overlay {
  display: none;
}
```

## 14. 数据模型

建议任务结构：

```js
{
  id: "uuid",
  pagePath: "C:/work/page.html",
  outputDirectory: "C:/work/exports",
  outputFilename: "page.mov",
  width: 3840,
  height: 2160,
  fps: 60,
  durationSeconds: 30,
  durationSource: "default-30s",
  status: "ready",
  progress: {
    currentFrame: 0,
    totalFrames: 1800,
    percent: 0,
    stage: "idle"
  },
  error: null
}
```

建议设置结构：

```js
{
  ffmpegPath: "",
  ffmpegVersion: "",
  lastOutputDirectory: "",
  defaultWidth: 3840,
  defaultHeight: 2160,
  defaultFps: 60
}
```

## 15. 实施阶段

### Phase 1: 仓库骨架

- 初始化 `Electron` 项目
- 建立 `main / preload / renderer / core` 目录
- 跑通主窗口显示
- 跑通设置文件读写

### Phase 2: 队列和参数

- 支持拖放 HTML
- 支持文件对话框导入
- 构建任务模型
- 显示队列
- 支持默认分辨率和帧率修改

### Phase 3: 首帧预览

- 完成隐藏渲染窗口
- 实现单页面加载
- 实现页面准备脚本
- 抓取首帧并在 UI 显示

### Phase 4: 完整抓帧与 MOV 导出

- 实现逐帧抓图
- 实现临时帧目录
- 实现 FFmpeg MOV 编码
- 实现进度和预览推送

### Phase 5: 停止与错误处理

- 实现停止逻辑
- 实现统一错误模型
- 确保临时目录可清理

### Phase 6: 平台与打包

- 完成 Windows、Linux、macOS 路径差异处理
- 完成 FFmpeg 探测
- 完成 macOS 能力探测
- 补全 Electron 打包配置

## 16. 验收标准

满足以下条件时，可认为第一阶段达成：

- 不安装系统 Chrome/Edge 也能启动应用
- 可拖入多个本地 HTML 文件并形成队列
- 可通过系统文件对话框多选导入 HTML
- 可调整分辨率和帧率
- 无 duration metadata 的页面按 `30s` 渲染
- 右侧预览区可看到真实渲染帧
- 进度区可显示百分比和帧进度
- 可显示并打开导出文件夹
- 可显示应用版本和 FFmpeg 版本
- 找不到 FFmpeg 时可以在设置中手动选择
- `导出`、`停止`、`打开导出文件夹`、`设置` 四个按钮均可用
- 渲染完成后生成透明 `MOV`
- 停止渲染后不会残留失控的 Chromium 渲染窗口或 FFmpeg 子进程

## 17. 风险与注意事项

- `capturePage()` 的透明通道行为需要实际验证
- 长时长高分辨率页面会产生大量 PNG，中间帧占用磁盘很快
- 本地字体差异会让不同平台渲染结果出现偏差
- macOS 的硬件编码能力与透明 ProRes 规格不一定完全匹配
- Linux 桌面环境差异会影响“打开导出文件夹”的行为
- 如果未来把 FFmpeg 一起打包，需额外处理许可证与分发方式

## 18. 后续 session 直接启动清单

未来在 `frontend2video` 仓库继续开发时，建议按以下顺序直接执行：

1. 把本文复制或移动到新仓库的 `app/docs/development-spec.md`
2. 初始化 Electron 项目骨架
3. 先实现 `main`、`preload`、`renderer` 三层连通
4. 再实现文件导入、队列模型和设置存储
5. 再实现隐藏渲染窗口和首帧预览
6. 再实现逐帧抓图与 FFmpeg MOV 编码
7. 最后补停止逻辑、平台探测和打包

如果未来 session 只读这一份文档，不再访问当前仓库，也应该足以开始实现。

## 19. 历史说明

本文是根据一条已有的本地 HTML 渲染原型抽象整理而成，但本文本身已经包含后续开发所需的关键行为定义、默认值、算法与模块边界。

换句话说：

- 历史原型可以帮助比对实现
- 但后续开发不应依赖必须去阅读历史原型代码才能动手
