# -
实现无人机通信心跳检测可视化
markdown
# 无人机心跳模拟与可视化

本项目模拟无人机心跳发送与接收过程，并通过 Streamlit 实现数据可视化。可用于测试心跳机制、超时检测逻辑以及数据展示。

## 功能特点

- 模拟每秒发送一个心跳包（含序号和时间戳）
- 支持丢包模拟（可调节丢包率）
- 自动检测超时：3秒未收到心跳则记录超时事件
- 生成事件时间线图和详细数据表格
- 数据可导出为 CSV 文件
- 基于 Streamlit 的交互式界面

## 快速开始

### 本地运行

1. 克隆仓库：
   ```bash
   git clone https://github.com/你的用户名/heartbeat-simulator.git
   cd heartbeat-simulator
安装依赖：

bash
pip install -r requirements.txt
运行应用：

bash
streamlit run app.py
在浏览器中打开 http://localhost:8501 即可使用。
