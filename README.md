# PYNQ-Z2 多频段音频加速器

基于 PYNQ-Z2 的多频段动态范围压缩（DRC）音频处理项目。仓库里包含 Python 参考实现、
HLS 的 FIR 核及其 C 仿真流程、把核和 PYNQ 音频 codec 接在一起的整机 overlay，
以及同一颗芯片上 ARM 与 PL 的对比实测。

**工具链**：PYNQ-Z2 镜像 2.7.0 + Vivado / Vitis HLS 2020.2，目标器件 `xc7z020clg400-1`。
仓库里所有上板实测和综合记录都是这一套跑出来的。

## 项目现状

- Python 参考基线用 48 kHz、4 段相减式分频、193 抽头线性相位低通。最近一次本地运行记在 `data/results/baseline_metrics.md`。
- 自研 HLS 核的单独综合与实现结果在 `data/results/impl_metrics.md`。
- 核已经和 PYNQ 音频 codec 一起集成进整机 overlay。`board/overlay/fir.bit` 就是它，由 `board/overlay/build_fir.tcl` 构建；`board/scripts/fir_core.py` 走 AXI-Lite 驱动这个核。在板子上，核处理了 144,000 个采集样本（18 块），直通结果按 96 拍群延迟逐位对齐、错配 0 个；但那次采集电平近乎静音，不能作为可听的麦克风压缩 A/B 证据。合成信号压缩检查通过。麦克风 → 核 → 耳机「边录边放」的真·实时通路已经跑通（`board/scripts/fir_live.py`）：连续 8/10/24 秒全部 1.00 倍、一块不丢，24 秒那档 2401 块、960000/960000 采样逐位自检通过；核接口没改，`fir.bit` 没重跑。
- 同芯片 ARM vs PL 加速比：**19.5 倍**（2026-10-08 板上实测，原始输出 `data/results/compare_cpu_fpga_run_20261008.txt`）。Zynq PS 侧的 ARM 每采样 **6.199 µs**（3.36× 实时），PL 里的核 **0.317 µs**（65.69× 实时；理论 0.280 µs = 28 拍 @ 100 MHz）。引用这个数必须带四条说明 —— 两边精度不同（float64 vs Q1.15）、CPU 那边是 scipy 的 C 实现、核的耗时里含每块约 297 µs 的 Python 开销（占长块实测 12%）、两边每采样都是 579 次有效乘法。全文见 `data/results/accel_cpu_vs_fpga.md`。
- 早期还测过一个开源参考 overlay。结果在 `data/results/reference_overlay_metrics.md`，和自研核无关，别混着引用。
- 当前英文海报在 `report/poster_en/pynq_multiband_audio_poster_v2.pptx`；早期 `pynq_multiband_audio_poster_draft.pptx` 已被它取代。海报明确把核级 OOC 资源数据和板级吞吐数据分开。
- Python/HLS/RTL 三实现对比还没做。`src/rtl/fir_lp.v` 已存在（只有 3 个低通，没有相减 / DRC / AXI 外壳），xsim 逐位对拍通过。

## 目录结构

| 目录 | 内容 |
| --- | --- |
| `src/python/` | Python 基线、系数与测试数据导出、比对与分析工具 |
| `src/hls/` | HLS 核与 C 测试台 |
| `src/rtl/` | 自研 RTL：`fir_lp.v`（3 个低通，无相减 / DRC / AXI 外壳） |
| `sim/hls_csim/` | C 仿真用的 FIR 系数文件 |
| `build/hls/` | HLS 与 Vivado 的脚本、指令文件和报告 |
| `board/` | PYNQ 笔记本、整机 overlay（`overlay/fir.bit`）、核驱动、板级脚本 |
| `data/audio/` | 测试音频与生成的音频 |
| `data/figures/` | 生成的分析图 |
| `data/results/` | 软件、仿真与实现的记录 |
| `skill/` | 提示词、模板、检查脚本、踩坑笔记 |
| `report/` | 设计报告、接口说明、提交清单、协作日志 |

## Python 基线

需要 Python 3.9 或更高。在仓库根目录用 PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-python.txt
python src/python/multiband_baseline.py --taps 193 --repeat 5
```

输入是 `data/audio/real_voice.wav`。脚本把它重采样到 48 kHz，跑 4 段 DRC 参考实现，
写出 `data/audio/multiband_output.wav` 和 `data/figures/multiband_comparison.png`。
想从这个输出生成时频瀑布图，再跑 `python src/python/plot_spectrum_waterfall.py`，
它会写出 `data/figures/spectrum_waterfall.png`。
打印出来的 CPU 计时是本地主机上的数，**不是板级加速比**。

重新生成当前 HLS 测试输入对应的系数和浮点黄金参考：

```powershell
python src/python/export_coefficients.py --taps 193
python src/python/export_golden.py --taps 193
```

## HLS C 仿真

项目的 C 仿真脚本用的就是 `build/hls/run_csim.tcl` 里写的那个 Vivado/Vitis HLS 安装路径。
在装好这套工具链的 Windows 机器上，从仓库根目录跑文档里那条批处理命令，
再把 `data/results/hw_output.txt` 和 `data/results/python_golden.txt` 做比对：

```powershell
$env:MSYS_NO_PATHCONV = '1'
cmd /c "E:\Xilinx\Vitis_HLS\2020.2\bin\vitis_hls.bat -f build\hls\run_csim.tcl"
python src/python/compare_golden_vs_hw.py
```

比对脚本会打印信噪比、最大绝对误差、采样对齐和直通模式的逐位结果。
存在有效 HLS 输出文件时，它还会存下 `data/figures/hls_golden_comparison.png`。

跑之前先确认装的是哪个版本、路径对不对。C 仿真会覆盖自己的输出文件，
需要留着比的旧结果先另存再跑。

## 板级工作与实测

`board/overlay/fir.bit` 是本项目的 overlay：自研多频段 HLS 核加 PYNQ 音频 codec。
`board/scripts/fir_core.py` 是寄存器级驱动；`board/scripts/fir_audio_loop.py`
从麦克风录音、过核、再放出来（一块一块处理）；`board/scripts/fir_live.py` 跑麦克风 → 核 → 耳机的真·实时通路；
`board/scripts/fir_selftest.py` 做直通和分块连续性检验；
`board/scripts/compare_cpu_fpga.py` 在同一块板子上量 ARM 基线与核的对比（2026-10-08 板上实测 **19.5 倍**，见 `data/results/accel_cpu_vs_fpga.md`）。

`overlay/ps_only.bit` 是更早的板级放音产物，PL 里是空的。
`overlay/audio.bit` 是更早的「我们自己建的工程能出声」里程碑产物，只有音频 codec。
这两个都不含加速核，别拿来当本项目的结果。

`data/results/reference_overlay_metrics.md` 记的是另一个开源参考 overlay，和自研核无关。
连同 `data/results/impl_metrics.md` 和 `data/results/accel_cpu_vs_fpga.md` 一起看，
才能弄清每个数的适用范围和局限。

## 许可

MIT，见 `LICENSE`。
