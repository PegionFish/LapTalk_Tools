# frontend2video

> 该工具现已并入 `LapTalk_Tools` 仓库，当前目录即 monorepo 内的 `frontend2video/` 子项目。

Electron 桌面工具，用于将本地 HTML 页面渲染为视频资产，首版目标是导出透明
`MOV / ProRes 4444`。

## 当前状态

- 已完成仓库骨架、主进程 / preload / renderer 三层连通代码
- 已完成任务队列、参数编辑、设置存储、FFmpeg 探测、隐藏渲染窗口、
  逐帧抓图和 MOV 编码主链路实现
- 已补纯 Node 单元测试与语法检查脚本
- 尚未在当前环境执行 Electron 真机运行验证，因为依赖尚未安装

## 手动安装依赖

当前环境未检测到 `pnpm`。如你希望沿用 `pnpm`，建议先手动执行：

```powershell
corepack enable
corepack pnpm install
```

若你暂时用 `npm`，也可以手动执行：

```powershell
npm install
```

## 可用命令

```powershell
npm run check
npm test
npm run smoke:render
npm run test:metadata-page
npm run dev
npm run pack
npm run dist
```

## 示例页面

- 带 metadata 时长的示例页面：
  `samples/metadata-timeline-demo.html`
- 可直接验证该页面的导出链路：
  `npm run test:metadata-page`

## 文档入口

- 根目录 spec：`2026-05-21-render-studio-development-spec.md`
- 实施方案：`plans/2026-05-21-render-studio-implementation.md`
- 打包说明：`app/docs/packaging.md`
