# PYNQ-Z2 多频段音频加速器

基于 PYNQ-Z2 的多频段动态范围压缩（DRC）音频处理项目。仓库里包含 Python 参考实现、
HLS 的 FIR 核及其 C 仿真流程、把核和 PYNQ 音频 codec 接在一起的整机 overlay，
以及同一颗芯片上 ARM 与 PL 的对比实测。

**工具链**：PYNQ-Z2 镜像 2.7.0 + Vivado / Vitis HLS 2020.2，目标器件 `xc7z020clg400-1`。
仓库里所有上板实测和综合记录都是这一套跑出来的。

## 当前演示版（2026-10-31）

演示用的是 **PL 直连**位流 `board/overlay/fir_pl.bit`：I2S 进、四段前端与压缩器全在 PL 里、
I2S 出，音频一个字节都不过 PS。网页验配界面由 `board/scripts/fir_pl_live.py` +
`fir_pl_webui.py` 提供。

| 项 | 值 |
|---|---|
| 位流 | `board/overlay/fir_pl.bit`，md5 `628b75b3d7a13015a69125fb8ac2d228` |
| 硬件描述 | `board/overlay/fir_pl.hwh`，md5 `7fa8e8b3b53aa8bcc9d7ecfac2f546a5` |
| 板上目录 | `/home/xilinx/dma_exp/` |
| 时序 / 资源 | WNS +0.090 ns，TNS 0，0 个失败端点（49,518 个端点全过）；DSP 213/220 |
| 网页 | `http://192.168.2.99:8080/` |

推位流（在仓库根目录，Git Bash；`.bit` 和 `.hwh` 必须同一次构建）：

```bash
MSYS_NO_PATHCONV=1 python board/probes/board_ssh.py --put board/overlay/fir_pl.bit /home/xilinx/dma_exp/fir_pl.bit
MSYS_NO_PATHCONV=1 python board/probes/board_ssh.py --put board/overlay/fir_pl.hwh /home/xilinx/dma_exp/fir_pl.hwh
```

启动（板上，顺序不能反：overlay 只能被一个进程持有）：

```bash
sudo env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u /home/xilinx/dma_exp/fir_pl_live.py &
sudo env XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u /home/xilinx/dma_exp/fir_pl_webui.py --port 8080
```

日常开关用服务（`pl_supervise.sh` 每 30 秒会把掉的进程拉回来，所以停服务必须用 systemctl，
只 `pkill` 最多管半分钟）：`systemctl start fir-pl-webui` / `systemctl stop fir-pl-webui`。

验收两条命令：

```bash
bash sim/run_all_checks.sh          # 无板：DRC 仿真、寄存器单测、lint、两个自检
bash board/probes/board_accept.sh   # 上板：寄存器窗口、压缩器、双耳处方、抓取窗口
```

`board_accept.sh` 会自己停服务、跑完起回来，跑的时候板子不能有别人在用。
各项的单独命令、期望输出和失败时看哪个日志，写在 `board/probes/` 各探针的文件头里。

### 演示素材与调参基线：这两个文件不在仓库里

板上播放列表（落在 `/home/xilinx/dma_exp/`）引用两个**私有文件**，随板子走、没进 Git。
复现时按右列替代：

| 板上文件 | 干什么用 | 仓库里拿什么替 |
|---|---|---|
| `wav/voice_20261031_184837.wav` | 播放列表里的人声素材，循环放 | `data/audio/demo_voice_loop_48k.wav`（48 kHz 单声道循环段）；想让四段分频更“听得出来”用 `data/audio/demo_bands_loop_48k.wav` |
| `tune_baseline.json` | 板上调参备份，重启后从这里恢复 `/dev/shm/fir_tune.json` | **没有数值备份**：能复现的是功能（四段增益 / 门限 / 比率 / MPO 都能运行期改），不是现场那一组具体数值 |

这两件都不影响判据：板载播放的验收看的是 `board/probes/ply_margin.py` 报的送数速率与
FIFO 余量（48828.12 Hz、`stall_n_ge_margin 0`），换任何 48 kHz 单声道 wav 都成立。

**换素材时看首尾接缝。** 首尾不衔接的片段每绕一圈会“啪”一声，是循环接缝不是 PL 的毛病
（10-31 排查时换成 `demo_voice_loop_48k.wav` 就没了）。

---

**上一代块模式版**：tag `v1.0-submission`，位流 `board/overlay/fir.bit`，md5
`5aa079f29e8d66c21d1d34f1302c3695`（`v25_env_indep`，每采样 36 拍）。这是 PS 在环的
块模式线，2026-10-23 的 16.7× 加速比就是在这颗上量的；它不是当前演示通路。

### 为什么是 2020.2，以及怎么复现

本项目用 Vivado / Vitis 2020.2，而不是更新的版本。两个硬要求是：脚本必须能复现，
位流和 PYNQ 镜像要经过实测。用 2020.2 的原因有两条：

1. PYNQ-Z2 的 2.7.0 镜像与 Vivado 2020.2 是同一代产品。镜像里的 `pynq` 包、overlay
   加载流程都是按这一代工具生成的，换成 2025.2 需要同时换镜像，而新镜像在本板上没有验证过。
2. 整机 overlay 里用到的 `audio_codec_ctrl` IP 是以 2020.2 的格式打包的，重新生成还要
   连带重验时序、位流和 600 秒实时稳定性。

复现不依赖工具的安装位置：`rebuild.sh` 用 `VIVADO_BIN` / `VITIS_HLS_BIN` 两个环境变量
指定可执行文件，三步构建（C 仿真 → 导出 IP → 整机位流）全部脚本化，详见
`BUILD_GUIDE.md`。换版本时需要同步改 `HLS_CFLAGS` 和 `build/hls/directives/`，
这两处就是全部与版本相关的配置。

## 项目现状

- Python 参考基线用 48 kHz、4 段相减式分频、193 抽头线性相位低通。最近一次本地运行记在 `data/results/baseline_metrics.md`。
- 自研 HLS 核的单独综合与实现结果在 `data/results/impl_metrics.md`。
- 核已经和 PYNQ 音频 codec 一起集成进整机 overlay。`board/overlay/fir.bit` 就是它，由 `board/overlay/build_fir.tcl` 构建；`board/scripts/fir_core.py` 走 AXI-Lite 驱动这个核。在板子上，核处理了 144,000 个采集采样（18 块），直通结果按 96 拍群延迟逐位对齐、错配 0 个；但那次采集电平近乎静音，不能作为可听的麦克风压缩 A/B 证据。合成信号压缩检查通过。麦克风 → 核 → 耳机「边录边放」的实时通路写好了（`board/scripts/fir_live.py`）：连续 8/10/24 秒全部 1.00 倍、一块不丢，24 秒那档 2401 块、960000/960000 采样逐位自检通过；核接口没改，`fir.bit` 没重跑。但 2026-10-13 记的「这条通路演不了：采集电平在约 0.7 秒里被推成满量程自激」**是错判** —— 那是低 24 位补码没做符号扩展的**读法假象**（数字零被读成 rms 46341 = 65535/√2），不是硬件、也不是通路问题。2026-10-15 话筒这一路查通、端到端跑成：麦克风 → 核 → 耳机 40 秒（4000 块 / 40.00 秒音频 / 40.19 秒墙钟 = 1.00 倍，逐位自检 960000/960000），进核 rms 中位 107 / 峰 24076，**操作者在耳机里听到了处理后的声音**。当时卡住的是 `HP + Mic` 口里那颗 U41（TI TS3A226AE 自动耳机开关，只在插头插入的上升沿检测一次并锁存）—— **演示前要先插拔到电平跳起来（实测要 18 秒）再开始，之后全程别碰插头**。注意那趟**四段增益全是 0.0 dB**（`LIVE_GAIN_DB` 的默认值），验的是通路成立。「四段各调各的、听感跟着变」当天晚上另外验了：一趟 66 秒的实时跑里用 `/dev/shm/fir_tune.json` 实时改单段增益六次（一段 0.0 ↔ −41.9 dB 交替），板上六次改动全接住、逐位自检在「增益变过 8 次」下仍 960000/960000、实时倍率 1.00 倍，**操作者当场确认听到了几次明显变化**（原始输出 `data/results/fir_gain_ab_run_20261015.txt`，复现脚本 `board/scripts/gain_ab_live.sh`）。⚠️ 但要记得：**对称设置（低频 +6 / 高频 −6）听不出来** —— 增益确实生效（电平比 1.00 → 1.23），可一升一降总响度几乎不变；**要让人听出来，得让单段的电平真的大幅改变。** 另：40 秒那趟墙钟多出 0.19 秒 ≈ 丢 18 块，耳机里会断续（搬样线程空转轮询占到 99% 预算）。
- 同芯片 ARM vs PL 加速比：**16.7 倍**（2026-10-23 板上实测，测的是块模式位流 `v25_env_indep`（`fir.bit`），原始输出 `data/results/compare_cpu_fpga_run_20261023.txt`）。Zynq PS 侧的 ARM 每采样 **6.117 µs**（3.41× 实时），PL 里的核 **0.367 µs**（56.77× 实时；理论 0.360 µs = 36 拍 @ 100 MHz）。2026-10-22 那次的 **12.5 倍**是 `v21_wdrc`（49 拍）的数，2026-10-08 的 **19.5 倍**是 `v19`（31 拍）的数，都别和现在这颗混用。引用这个数必须带四条说明 —— 两边精度不同（float64 vs Q1.15）、CPU 那边是 scipy 的 C 实现、核的耗时里含每块约 60 µs 的 Python 开销（占长块实测不到 10%）、两边每采样都是 579 次有效乘法。全文见 `data/results/accel_cpu_vs_fpga.md`。
- 早期还测过一个开源参考 overlay。结果在 `data/results/reference_overlay_metrics.md`，和自研核无关，别混着引用。
- Python / HLS / RTL 三实现对比已经做完，同一负载、同一精度契约、同一 10 ns 约束，表在
  `data/results/rtl_vs_hls.md`。自研 RTL 从单低通一路补到和 v25 核同功能：`src/rtl/fir_mb_fe.v`
  （3 个低通 + 相减 + 4 段综合，每采样 3 拍）、`fir_drc4.v`（四段压缩）、`fir_rtl_shell.v`
  （AXI-Lite + BRAM 块模式外壳）、`fir_stream_shell.v`（AXI-Stream 外壳）都上了板，
  块模式 14407 拍、DMA 版 14408 拍，两版逐位相同；65535 点连发 25.7 M 采样/秒。
  另有一颗只出握手时序图的抓取位流。**这三颗位流不在演示通路上**，
  范围和限制见该文件 §9，跑法见 `sim/rtl_sim/README.md` 和 `board/README.md`。
- 2026-10-25 晚上还起了第二条线：把音频数据从 PS 手里挪出来。四颗新位流
  （只有 DMA / DMA + v25 核 / 全 PL 直连 / 带内置音源的自环诊断版）里，
  DMA + 核那版口到耳 14.4~14.8 ms、连跑 60 秒零丢字；全 PL 直连按各级缓冲推算约 2.3 ms。
  全 PL 直连这一颗后来补上寄存器窗口和网页验配（`fir_pl_regs`），成为**当前演示通路**
  （见上文「当前演示版」）；其余几颗不在演示通路上。2.3 ms **是推算不是实测**，公式里的
  组延迟项还少算了一半，所以现在只报分解：数字段 1.97 ms（96 个采样）+ 声学 1.23 ms（10-22 实测）。
  数字、口径和哪些还不成立，全部写在 `data/results/dma_pl_direct_20261025.md`。

## 目录结构

| 目录 | 内容 |
| --- | --- |
| `src/python/` | Python 基线、系数与测试数据导出、比对与分析工具 |
| `src/hls/` | HLS 核与 C 测试台 |
| `src/rtl/` | 自研 RTL：前端、四段压缩、两种 AXI 外壳、握手监视器与抓取器、自写 I2S 收发与全 PL 直连顶；索引见 `sim/rtl_sim/README.md` |
| `sim/hls_csim/` | C 仿真的测试台与 FIR 系数文件 |
| `sim/rtl_sim/` | RTL 仿真的 testbench、参考模型、黄金向量与逐位比对脚本，跑法见该目录的 `README.md` |
| `build/` | 构建脚本、HLS 指令文件、各版本综合与实现报告；索引见 `build/README.md` |
| `board/` | 整机 overlay（演示用 `overlay/fir_pl.bit`，上一代 `overlay/fir.bit`）、核驱动、板级脚本、主机侧探针（`probes/`）；索引见 `board/README.md` |
| `data/audio/` | 测试音频与生成的音频 |
| `data/figures/` | 生成的分析图 |
| `data/results/` | 所有实测读数；**哪个文件是当前结论、哪些不能引用，见 `data/results/README.md`** |
| `skill/` | 提示词、模板、校验脚本、踩坑笔记；索引见 `skill/README.md` |
| `report/` | **设计报告（`design_report.md`）**、合规对照与提交清单（`SUBMISSION_CHECKLIST.md`）、寄存器级接口说明（`hardware_interface_spec.md`）、大模型协作日志（`llm_collab_log/`）、板端截图（`img/`）、海报源文件（`poster/`）；演示视频在 [Release `demo-20261101`](https://github.com/JR0425/pynq-multiband-audio-accel/releases/tag/demo-20261101)（59.5 MB，不进 Git 历史） |

**先读哪一份**：要了解项目全貌看 `report/design_report.md`（一页速览 + 前人工作 + 优化轨迹 +
验证链）；要核对是否符合赛道要求看 `report/SUBMISSION_CHECKLIST.md`；要动手跑看本文件
开头的「当前演示版」两条命令。

参考模型、系数生成、质量指标和演示素材生成脚本在 `sim/model/`，板子调试探针在 `board/probes/`。

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

在装好这套工具链的机器上，从仓库根目录跑 `./rebuild.sh csim`，它调用
`build/hls/run_csim.tcl`（工具位置用 `VITIS_HLS_BIN` 指定，不写死在脚本里）。
跑完再把 `data/results/hw_output.txt` 和 `data/results/python_golden.txt` 做比对：

```bash
./rebuild.sh csim
python src/python/compare_golden_vs_hw.py
```

比对脚本会打印信噪比、最大绝对误差、采样对齐和直通模式的逐位结果。
存在有效 HLS 输出文件时，它还会存下 `data/figures/hls_golden_comparison.png`。

跑之前先确认装的是哪个版本、路径对不对。C 仿真会覆盖自己的输出文件，
需要留着比的旧结果先另存再跑。

## 板级工作与实测

演示通路是 `board/overlay/fir_pl.bit`（全 PL 直连），驱动和网页在
`board/scripts/fir_pl_live.py`、`fir_pl_webui.py`，启动命令见开头「当前演示版」。
上一代块模式 overlay 是 `board/overlay/fir.bit`：自研多频段 HLS 核加 PYNQ 音频 codec。
`board/scripts/fir_core.py` 是它的寄存器级驱动；`board/scripts/fir_audio_loop.py`
从麦克风录音、过核、再放出来（一块一块处理）；`board/scripts/fir_live.py` 跑麦克风 → 核 → 耳机的实时通路（2026-10-15 已端到端跑通并能听到处理声，见上）；
`board/scripts/fir_selftest.py` 做直通和分块连续性检验；
`board/scripts/compare_cpu_fpga.py` 在同一块板子上量 ARM 基线与核的对比（2026-10-23 板上实测 **16.7 倍**，见 `data/results/accel_cpu_vs_fpga.md`）。

`overlay/fir_rtl.bit`、`fir_rtl_dma.bit`、`fir_rtl_dma_cap.bit` 是 2026-10-25 的自研 RTL 实验位流（块模式 / DMA + AXI-Stream / 只出握手时序图），由 `overlay/build_rtl*.tcl` 构建，探针是 `board/scripts/rtl_*.py`。它们**不在演示通路上**；位定义和跑法见 `board/README.md`。

同一天晚上还有一组四颗，做的是"把音频数据从 PS 手里挪开"：
`overlay/fir_dma.bit`（自写 I2S + `axi_dma`，不含核，用来量这条路的结构地板）、
`overlay/fir_dma_core.bit`（同上再加 v25 核）、
`overlay/fir_pl.bit`（全 PL 直连，音频一个字节都不出 PL）、
`overlay/i2sdbg.bit`（内置 440 Hz 音源 + 片内自收 + 翻转计数，是诊断用的仪器位流）。
其中 `fir_pl.bit` 后来补了寄存器窗口和网页验配，就是当前演示通路；其余三颗不在演示通路上。
构建脚本是 `overlay/build_audio_dma.tcl`（`FIR_AUDIO_DMA_WITH_FIR` / `FIR_AUDIO_DMA_TX_AW`
选版本）和 `build_audio_pl.tcl`，探针在 `board/scripts/dma_*.py` / `pl_*.py` / `core_dump.py`。
数字和适用范围见 `data/results/dma_pl_direct_20261025.md`。

`overlay/ps_only.bit` 是更早的板级放音产物，PL 里是空的。
`overlay/audio.bit` 是更早的「项目自建工程能出声」里程碑产物，只有音频 codec。
这两个都不含加速核，别拿来当本项目的结果。

`data/results/reference_overlay_metrics.md` 记的是另一个开源参考 overlay，和自研核无关。
连同 `data/results/impl_metrics.md` 和 `data/results/accel_cpu_vs_fpga.md` 一起看，
才能弄清每个数的适用范围和局限。

## 许可

MIT，见 `LICENSE`。
