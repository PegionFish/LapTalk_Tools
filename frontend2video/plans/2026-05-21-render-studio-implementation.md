# Render Studio 实施方案

## 背景

当前仓库仅包含开发规格文档与基线说明，尚无应用代码。目标是按
`2026-05-21-render-studio-development-spec.md` 落地一版可运行的
`Frontend2Video` Electron 桌面应用。

## 范围

- 初始化 Electron 项目骨架与打包配置
- 建立 `main / preload / renderer / core` 模块结构
- 实现 HTML 导入、任务队列、参数编辑、设置存储
- 实现隐藏渲染窗口、首帧/实时预览、逐帧抓图、FFmpeg MOV 编码
- 实现停止、错误处理、输出目录选择与打开
- 补充基础文档与纯 Node 单元测试

## 依赖策略

- 运行时依赖：`electron`
- 开发依赖：`electron-builder`
- 不额外引入 `uuid`，改用 Node/Electron 内置 `crypto.randomUUID()`，
  减少依赖面，同时满足唯一任务 ID 需求

## 实现拆分

1. 主进程层
   - 应用启动
   - 主窗口与隐藏渲染窗口
   - IPC 注册
   - 队列 / 设置 / 渲染服务编排
2. 预加载层
   - 暴露安全 API
   - 统一订阅主进程事件
3. 渲染器层
   - 队列视图
   - 预览视图
   - 参数编辑
   - 设置弹窗
   - 拖放导入
4. 核心层
   - HTML 校验
   - 时长与帧数解析
   - 输出目录推断
   - 页面准备脚本
   - 临时工作区
   - FFmpeg 探测 / 编码
   - 渲染循环

## 验证策略

- `node --check`：检查所有 JS 文件语法
- `node --test`：运行纯 Node 单元测试
- Electron 运行与打包命令写入 `package.json`，依赖安装由用户手动执行

## 已知限制

- 当前环境未检测到 `pnpm`，后续安装依赖需用户手动执行
- 未安装依赖前无法实际启动 Electron 或验证 FFmpeg/Chromium 集成链路
- `capturePage()` 的透明通道行为仍需在安装依赖后进行真机验证
