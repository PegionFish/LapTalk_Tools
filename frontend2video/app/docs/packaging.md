# Packaging Notes

## 当前策略

- 打包工具：`electron-builder`
- 输出平台：Windows、macOS、Linux
- Chromium：随 Electron 一起分发
- FFmpeg：第一阶段不强制打包进安装包，优先走探测 + 手动设置

## 后续待验证

- Windows 安装包签名
- macOS 权限、签名与公证
- Linux 打开导出目录的桌面环境兼容性
- 若内置 FFmpeg，需要额外补许可证与分发说明
