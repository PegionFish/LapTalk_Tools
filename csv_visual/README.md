# HWiNFO CSV 折线图导出工具

一个面向 Windows GUI 的小工具，用来读取 HWiNFO 导出的 CSV 文件，按时间戳绘制指定参数的折线图，并导出透明背景 PNG，方便接入后续开发流程。

## 功能

- 读取 HWiNFO CSV 文件
- 以 `Date + Time` 组合出的时间戳作为 X 轴
- 以指定参数的数值作为 Y 轴
- 支持多选参数并在同一张图里叠加显示
- 导出透明背景 PNG
- 支持中文列名、重复列名和列名筛选

## 环境

- Windows
- Python 3.10+
- `matplotlib`

安装依赖：

```powershell
pip install -r requirements.txt
```

## 启动

双击 `run_gui.bat`，或者手动执行：

```powershell
python .\main.pyw
```

## 使用方式

1. 点击“浏览...”选择 HWiNFO CSV 文件
2. 在左侧筛选并多选要绘制的参数
3. 按需设置标题、输出宽高和 DPI
4. 点击“预览图表”
5. 点击“导出透明 PNG”

## 说明

- 图表背景为透明，但坐标轴、文字、网格线和曲线会保留
- 如果 CSV 中存在重名列，界面会显示 CSV 列号和重复序号来区分
- 当前仓库已适配样例文件 `R23-15.CSV`

