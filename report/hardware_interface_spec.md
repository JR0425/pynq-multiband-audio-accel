# 硬件接口需求规格（Python 侧）

本文件定义了 Python 侧（Jupyter Notebook / 上板脚本）对 HLS/RTL 加速核的接口需求。硬件队友在搭建 AXI-Lite 和 AXI-Stream 时，请参照以下寄存器映射，确保 Python 能够正确配置和调用。

## 1. AXI-Lite 配置寄存器（Python 通过 overlay 读写）

| 寄存器名称 | 偏移地址 | 读写 | 位宽 | 功能说明 |
| :--- | :--- | :--- | :--- | :--- |
| CTRL | 0x00 | R/W | 32 | bit0: Start (1=启动); bit1: Done (1=完成) |
| NUM_TAPS | 0x04 | R/W | 32 | FIR 滤波器抽头数（预期 65） |
| FREQ_BAND_1 | 0x08 | R/W | 32 | 频段 1 截止频率（0-300Hz） |
| FREQ_BAND_2 | 0x0C | R/W | 32 | 频段 2 截止频率（300-600Hz） |
| FREQ_BAND_3 | 0x10 | R/W | 32 | 频段 3 截止频率（600-1000Hz） |
| FREQ_BAND_4 | 0x14 | R/W | 32 | 频段 4 截止频率（1000-8000Hz） |
| COMP_THRESH | 0x18 | R/W | 32 | DRC 压缩阈值（默认 0.1） |
| COMP_RATIO | 0x1C | R/W | 32 | DRC 压缩比（默认 0.7） |

## 2. AXI-Stream 数据流（音频通路）

- **输入**：AXI-Stream Slave，32-bit 定点（Q1.15 或浮点），采样率 48kHz。
- **输出**：AXI-Stream Master，与输入同格式。
- **TLAST**：每处理完一帧（如 1024 个样本）拉高一次，方便 Python 侧 DMA 接收。

## 3. Python 侧调用逻辑（Jupyter 模板）

```python
# 1. 加载 overlay
overlay = Overlay("audio_accel.bit")
# 2. 写入寄存器
overlay.audio_accel.write(0x04, 65)       # NUM_TAPS
overlay.audio_accel.write(0x18, 0.1)      # 压缩阈值
overlay.audio_accel.write(0x1C, 0.7)      # 压缩比
# 3. 启动加速核
overlay.audio_accel.write(0x00, 0x01)
# 4. 等待完成
while (overlay.audio_accel.read(0x00) & 0x02) == 0:
    pass
print("硬件处理完成")