# DMA 与全 PL 直连（2026-10-25 晚）

这一批是出货版 `fir.bit` 之外的四颗位流，做它们的目的只有一个：把音频数据从
「PS 逐字搬」这条路上挪开，看延迟能压到多少。**它们都不在提交的演示通路上**，
出货演示仍然是 `fir.bit` + 网页验配界面。

先说结论再给数：**DMA 这条线省了约 1.5 ms，PL 直连这条线能省约 12 ms，
但 PL 直连还没有验配接口、也没做过人耳验收**。

## 1. 四条通路的结构差别

| 版本 | 位流 | 音频数据怎么走 | 每块 PS 做什么 |
| --- | --- | --- | --- |
| 出货 | `fir.bit` | codec ↔ 厂商 `audio_codec_ctrl`（AXI-Lite）↔ 核 | 逐字搬：每块 MMIO 读写一整块 |
| 只有 DMA | `fir_dma.bit` | codec ↔ 自写 `i2s_duplex` ↔ `axi_dma` ↔ DDR | 只写地址和长度 |
| DMA + 核 | `fir_dma_core.bit` | 同上，PS 在 DDR 上把块调一次核 | 写地址长度 + 调一次核 |
| PL 直连 | `fir_pl.bit` | codec ↔ `i2s_duplex` ↔ `fir_mb_fe` ↔ `fir_drc4` ↔ codec，**全程不出 PL** | 配一次 codec，之后只读状态 |

`fir_dma_tx9.bit` 是 `fir_dma` 的一版对照：只把播放 FIFO 从 1024 字压到 512 字
（`FIR_AUDIO_DMA_TX_AW=9`）。播放侧深度直接就是延迟，压小确实更快，但积压余量
不够，最后保留 1024 字。这颗位流留着，是因为「延迟从哪来」这个结论靠它。

## 2. 数字

| 指标 | 值 | 证据等级 |
| --- | --- | --- |
| `fir_dma.bit` 端到端（不含核） | **10.1 ms** | 板上实测。这是"DMA 这条路的结构地板" |
| `fir_dma_core.bit` 口到耳 | **14.4 ~ 14.8 ms** | 板上实测，多次重复都在这个区间 |
| `fir_dma_core.bit` 连续跑 60 秒 | overrun = 0，underrun = 0 | 板上实测，两种模式各一趟 |
| `fir_pl.bit` 连续跑 60 秒 | 零丢字、零断流 | 板上实测，两种模式各一趟 |
| `fir_pl.bit` 端到端 | **约 2.3 ms** | **推算，不是实测**。按各级 FIFO 水位 + 核内拍数 + codec 的固定转换时延算出来的预算 |
| 出货 `fir.bit` 块模式 4800 点往返 | 2.17 ms（约 46× 实时） | 板上实测，另一套口径，不能和上面的"口到耳"并列 |

延迟差怎么来的：出货版走厂商音频 IP，那个 IP 没有 DMA，PS 必须逐字搬，
搬完才能处理下一块 —— 演示档位 16.2 ms 主要就是这个搬运和分块撑起来的。
换 DMA 之后 PS 只写地址，14.4 ms 里剩下的大头是分块本身的缓冲（录音 4096 字 /
播放 1024 字）。PL 直连把缓冲压到几十个字，所以是另一个量级。

**这 12 ms 的差别是真差别，但只在这四颗位流上都还有未完成的事时才成立** ——
见 §5。

## 3. 核的输出有没有变

DMA 版里放的是同一个 `v25_env_indep` HLS 核（`build_audio_dma.tcl` 里
`create_bd_cell -type ip -vlnv xilinx.com:hls:fir_multiband:1.0`，
`FIR_AUDIO_DMA_WITH_FIR=1` 时才有这一句），寄存器也一样，所以理论上必须逐位相同。
"理论上"不算证据，`board/scripts/core_dump.py` 就是证据：同一份 `in_q15.mem`、
同一个驱动，分两次进程分别加载 `fir.bit` 和 `fir_dma_core.bit`，落盘后逐字节比。
两次都要单独起进程 —— 前一块的 UIO 还开着时灌新位流会卡住。

## 4. 怎么复现

位流和探针要放在板上**同一个目录**（`Overlay()` 只认同名 `.bit` / `.hwh`，
地址靠 hwh 解析）。本轮用的是 `/home/xilinx/dma_exp/`。

```bash
# 分段验收：a 内部自环 / b 只录音 / c 只放音 / d 整条回环
echo xilinx | sudo -S env XILINX_XRT=/usr \
    /usr/local/share/pynq-venv/bin/python3 dma_audio_probe.py --stage a
#   ... --stage b --frames 480     --stage c --tone 440 --secs 5
#   ... --stage d --frames 480 --secs 60

# 带核的整条链路（60 秒，口到耳）
echo xilinx | sudo -S env XILINX_XRT=/usr chrt -f 60 \
    /usr/local/share/pynq-venv/bin/python3 dma_fir_loop.py --secs 60

# 全 PL 直连：只读状态，不写任何采样
python3 pl_direct_probe.py --secs 60            # 过压缩
python3 pl_direct_probe.py --secs 60 --drc 0    # 只过前端

# 核输出对拍
python3 core_dump.py --bit fir.bit          --mem in_q15.mem --out /tmp/core_fir.npy
python3 core_dump.py --bit fir_dma_core.bit --mem in_q15.mem --out /tmp/core_dma.npy
python3 core_dump.py --compare /tmp/core_fir.npy /tmp/core_dma.npy
```

`chrt -f 60` 不是可选项。板上有 `ksoftirqd-rt-refresh.service` 把 `ksoftirqd`
和十几个 `kworker` 提到了 `SCHED_FIFO 50`，普通优先级的进程会被一口气挤开
10~30 ms；录音 FIFO 的积压一旦涨上去就再也不降（每块进出一块，是个中性平衡），
后面整段都背着那十几毫秒。不套 `chrt` 时 60 秒断流几十到几百次，套上之后 0 次。

**原始终端输出没有单独存档。** 这一批是当晚一边调一边量的，读数在上面这几张表里，
复现命令就是本节这几条。

## 5. 没结的问题：板子的输出侧当晚不出声

这一节的顺序很重要，因为它推翻了当晚早些时候的一个结论。

1. `i2sdbg.bit`（`i2s_duplex` 加内置 440 Hz 音源 + 片内自收 + 翻转计数）读数证明
   **PL 在 `sdata_o` 上确实在送波形**：翻转计数每 2 秒涨约 63,000；自收端解出的值
   是 ±2²¹ 的方波；静音基线是全零；写入字数约 97.7 k 字/秒，和 48.828 kHz 帧率吻合。
2. 最终布局报告里 `sdata_o` 落在 G18、`bclk` R18、`lrclk` T17、`sdata_i` F17，
   全部 `FIXED`，和出货构建逐脚一致 —— 引脚落点不是原因。
3. codec 寄存器逐条比对，和已知能出声的那条路只差 7 个寄存器，耳机音量两路都是
   未静音（只差音量数值 57 vs 40）。
4. **A/B 对照**：同一个 codec、同一副耳机、两轮 10 秒 440 Hz，第一轮走**出货的
   `fir.bit`**，第二轮走 `i2sdbg.bit` —— **两轮都没有声音**。

第 4 条就是结论：**出货位流当时也放不出声，所以"我们的 PL 设计没输出"这个判断
在这一刻站不住**。输出侧整体是哑的，问题在耳机 / 插座 / codec 输出这一段，
不在我们写的东西里。

板载 `HP + Mic` 口里那颗 U41（TI TS3A226AE 自动耳机开关）只在插头插入的上升沿
检测一次就锁存，这是这个项目之前踩过的坑，当时要反复插拔到电平跳起来才出声。
当晚没有回到那个状态。

**下一步（准备好但没跑）**：`tools/board_debug/codec_jack.py` —— 把 `0x4000`~
`0x4029` 按名字读一遍，再看 `0x40E0`~`0x40EF` 的状态位，判断 codec 认为耳机插没插、
DAC 和耳机放大器有没有上电。这一步之前，上面所有版本**都不能算做过人耳验收**。

## 6. 仿真侧

- `bash sim/rtl_sim/run_xsim_duplex.sh`：`i2s_duplex` 的单元仿真，逐字对拍通过
  （收 78 字、TLAST 错 0、左右声道各自解对、ovf/und 全 0）。这颗模块是上面四颗
  位流的共同底座，改它之后先跑这一条再上板。
- `bash sim/rtl_sim/run_xsim_pl.sh`：`fir_pl_direct` 的诊断台，**不出 PASS/FAIL**，
  输出是内部相位的逐段快照。它是为了复现板上「flush 之后立刻放行，数据不出」的
  现象写的，**但仿真里复现不出来** —— `run+20us` 之后 `out_vld` / `fifo_wr` 一直在涨。
  所以那个现象不是状态机的逻辑错误，要么是板级的，要么和 §5 那个哑掉的输出侧是同一件事。
- `i2s_duplex_dbg.v` 里 `pframe` 一直是 0，是**我自己的插桩 bug**：`P_READ_R`
  在同一拍跳回 `P_WAIT_L`，`p_frame_done` 永远等不到那个沿。只影响这一个读数，
  `pl` / `pr` / `tog` / `wr` 都是对的。

## 7. 引用口径

- **14.4 ms / 10.1 ms / 60 秒零丢字** 只描述 `fir_dma*` 这三颗实验位流，
  不是出货版的数，不能和 `16.2 ms`（演示档）、`2.17 ms`（块模式 4800 点）、
  `56.77×`（核单独）并列。
- **`fir_pl.bit` 的 2.3 ms 是推算**，写报告必须写"推算"两个字。它没有端到端实测，
  也从来没有做过人耳验收。
- **`fir_pl.bit` 没有增益寄存器。** 四段增益和 DRC 参数都是编译期写死的模块参数，
  `ctrl` / `stat` 里只有运行、自环、flush、清零、DRC 使能这几个位。
  也就是说**网页验配那一整套（滑杆 / 预设 / 测听闭环）驱动不了它** ——
  它要变成可演示的东西，第一步是把增益做成 AXI-Lite 可写，不是换个位流。
- 这四颗位流用的都是同一套 `i2s_duplex` + `axis_fifo`，源码在 `src/rtl/`，
  构建脚本是 `board/overlay/build_audio_dma.tcl`（`FIR_AUDIO_DMA_WITH_FIR`、
  `FIR_AUDIO_DMA_TX_AW` 两个环境变量选版本）和 `build_audio_pl.tcl`
  （`FIR_PL_TX_TARGET` 选播放 FIFO 水位）。
