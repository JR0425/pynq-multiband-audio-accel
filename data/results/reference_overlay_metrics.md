# 参考 overlay 板级实测数据（Reference Overlay Metrics）

**数据来源**：开源参考项目 `soheilbh/fir_accel_pynq`，27 抽头 FIR，含 AXI-DMA 通路。

**这批数据的用途**：验证「用 Python 在 PYNQ 上加载 overlay、指挥 FPGA 处理音频」这条通路本身可行。


**不计入本项目的性能指标。** 本项目的软硬件对比数据以自研 HLS / RTL 加速核为准，
另见 `baseline_metrics.md`。

测试日期：2026-09-26
测试环境：PYNQ-Z2（xc7z020clg400）+ PYNQ-Z2 v2.7.0 镜像
输入数据：200,000 点 int32，100 MHz 采样，200 kHz 主信号 + 12 MHz / 46 MHz 噪声

## 一、功能验证（硬件输出 vs 软件参考）

| 测试项 | 结果 |
| :--- | :--- |
| DMA 搬运 20 万点耗时 | 约 3.1 ms |
| 最佳对齐位移 | +13 个采样点 |
| 该位移下的相关系数 | 1.000000 |
| 结论 | 与软件参考结果一致，仅存在 FIR 固有群延迟 |

**关于 +13**：该 FIR 为 27 抽头，线性相位 FIR 的群延迟为 `(N−1)/2 = 13`。
逐点直接比对会得到一个很低的相关系数、看起来像算错了 ——
必须先把位移扫齐，再看最佳位移是否等于理论群延迟。

## 二、耗时对比（同一任务，板上实测）

| 实现方式 | 耗时 | 备注 |
| :--- | :--- | :--- |
| Python 软件实现 | 0.0922 s | 板上 CPU 单线程 |
| FPGA 硬件加速 | 0.00469 s | 经 AXI-DMA 搬运 |
| 加速倍数 | **约 19.67 倍** | |

**口径说明**：另有一版「带驱动」的写法实测 **0.1064 s**，比纯软件还慢。
原因是该版本每次调用都重新申请连续内存，把内存申请的时间也算了进去。

## 三、证据

- `report/img/log-006-ref-overlay-sw-fir.png` —— 软件版波形与滤波效果
- `report/img/log-007-ref-overlay-input-signal.png` —— 输入信号的构造方式
- `report/img/log-008-ref-overlay-hw-accel.png` —— 硬件耗时与加速倍数

完整过程见 `report/llm_collab_log/2026-09-26_reference_overlay_on_board.md`。
