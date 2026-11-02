# 本代演示通路的加速比实测（2026-11-02）

## 结论

**160×**：同一颗 Zynq、同一串输入、每采样同样 579 次乘加。

| 项 | 值 |
|---|---|
| ARM（Cortex-A9，float64 + scipy） | **6.20 µs/采样** |
| PL 墙钟（含 AXI DMA 与驱动，65535 点一块） | **0.0387 µs/采样 = 25.8 M 采样/秒** |
| PL 核内（只看 `stat.total_cycles`） | **0.0300 µs/采样 = 33.3 M 采样/秒（3.000 拍/采样 @100 MHz）** |
| 加速比（墙钟） | **160×**（四次连跑 158.9—161.3） |
| 加速比（只算核内） | **207×**（206.5—207.4） |

四次连跑，同一块板、同一次开机，前两次与后两次分别用两颗位流：

| 趟 | 位流 | ARM | PL 墙钟 | PL 核内 | 墙钟比 | 核内比 |
|---|---|---|---|---|---|---|
| 1 | 10-25 版 | 6.195 µs | 0.0388 µs | 0.0300 µs | 159.7× | 206.5× |
| 2 | 10-25 版 | 6.204 µs | 0.0387 µs | 0.0300 µs | 160.3× | 206.8× |
| 3 | 11-02 重编版 | 6.196 µs | 0.0390 µs | 0.0300 µs | 158.9× | 206.5× |
| 4 | 11-02 重编版 | 6.221 µs | 0.0386 µs | 0.0300 µs | 161.3× | 207.4× |

两版位流的差别在 DRC 与增益级的实现（见下），吞吐完全一样，说明 3 拍/采样由前端
`fir_mb_fe` 决定，后级改动不影响。ARM 那 6.20 µs 与 10-23 块模式测的 6.117 µs
相差 1.3%，是同一台机器上的同一段代码。

## 为什么用 `fir_rtl_dma.bit` 而不是演示位流

演示位流 `fir_pl.bit` 里**没有 PS ↔ PL 的批量接口**：音频从 I2S 进、从 I2S 出，
PL 每 2048 拍（20.48 µs）固定消费一个采样，PS 插不进去。在 `fir_pl.bit` 上计时，
量到的是采样率，不是计算时间。

`board/overlay/build_rtl_dma.tcl` 编的这颗位流用的是**同一组 RTL 源文件**：

| | 演示位流 | 本次测量用的位流 |
|---|---|---|
| 构建脚本 | `build/overlay/build_audio_pl.tcl` | `build/overlay/build_rtl_dma.tcl` |
| 计算核 | `src/rtl/fir_mb_fe.v` + `src/rtl/fir_drc4.v` | 同一对文件 |
| 外壳 | `fir_pl_direct`（I2S 直连，不碰 DDR） | `fir_stream_shell` + `axi_dma` |

两个脚本 `add_files` 的就是这两个文件。**被量的那一侧没变**，只有数据怎么进出变了。
而且 DMA 外壳每块要额外花 0.56—0.59 ms（65535 点，折 8.6—9.0 ns/采样）——
演示通路没有这段开销，所以这个 160× 对演示通路是**偏保守**的。

### 为什么要重编一颗

仓库里原来那颗 `fir_rtl_dma.bit` 是 10-25 编的，而演示位流 10-31 用过新 RTL 重编。
查了源码历史：

| 文件 | 10-25 之后改过没有 |
|---|---|
| `src/rtl/fir_mb_fe.v` | **没有**（最后一次实质修改 10-20；10-30 只动注释） |
| `src/rtl/fir_drc4.v` | 改过：10-28 四段增益改运行期端口、10-29 双耳两份、10-31 软膝限幅 |
| `src/rtl/fir_stream_shell.v` | 改过：10-28 接运行期端口 |

3 拍/采样由前端决定，所以旧位流的数也不会错；但"和演示通路同一版 RTL"这句话
当时不成立。11-02 用 `build_rtl_dma.tcl` 按 HEAD 重编了一颗：

```
### WNS_ns = 0.209   DSP48 = 201   LUT = 11678   FF = 8105
md5 929ce60f0ab41001c47a82d0e71fe0ae
```

（DMA 版只有一个声道，所以 DSP 201 比演示位流的 213 少——演示是双声道两份 DRC。）
重编后吞吐不变，表 3、表 4 两趟就是这颗的读数。

## 位流本身是对的

速度只有在算对的前提下才有意义。逐位比对的结论：

- **重编位流的输出 = 当前 RTL 的 xsim 输出**。用 `in_q15.mem` 的 4800 点跑板上
  `rtl_dma_probe.py --drc`，与 `sim/rtl_sim/xsim_work_long_4800_loud/shell_out.txt`
  逐点相同：都只在 16 个点上偏离仓库里的黄金值，而且这 16 个点的数值完全一样
  （[317] 23080、[318] 23394、[321] 23573…）。硬件忠实实现了当前 RTL。
- 那 16 个点不是硬件错，是**仓库里的参考模型没跟上 10-31 的软膝限幅**：
  `run_xsim_long.sh` 里那个 Python 定点模型仍是"超过门限线性压"的写法。
  数据上两边同向、同位置，差的是限幅那一段的算法。

这留下一个待办（不在本次测量范围内）：`sim/rtl_sim/golden_n4800_drc_loud/` 与
`run_xsim_long.sh` 里的 DRC 参考模型还是 10-25 的，今天跑
`run_xsim_stream.sh DRC=1` 或 `rtl_dma_probe.py --drc` 会报 16 点不同。
要么把模型改成软膝，要么确认 RTL 该退回去——需要按设计意图定，不能靠测出来猜。

## 方法

探针 `board/probes/accel_demo_chain.py`，一次进程里两边背靠背跑：

1. **造输入**：与 `compare_cpu_fpga.py` 同一个种子（20261001）、同一组 11 根谱线
   （120—4000 Hz）+ 底噪 0.01，量化成 int16，峰值 29.8% FS。两边用同一串数。
2. **ARM**：3 组 193 抽头 `scipy.signal.lfilter` + 延迟支路相减，四段各自过
   `drc()` 再相加 —— 与核里那个循环同形状。预热一次，跑 3 趟取最快。
3. **PL**：加载 `fir_rtl_dma.bit`，先冷启动一块不算，再连发 N × 65535 点，
   `ctrl[0]=1` 走 DRC 通路，`verify=False`（Cortex-A9 上整块比对比传输本身还慢，
   会把数据通路的速度淹掉）。墙钟取 `perf_counter` 包住两次 `transfer()` + 两次
   `wait_dma`；核内取 `stat` 的 `total_cycles`。

11 根谱线 + 底噪的输入让压缩器真的在工作（门限 0.1 = 10% FS，输入峰值 29.8%），
两边都不是空跑。

`--passes 10` 与 `--passes 30` 各跑一遍，读数差别在 1% 以内，说明不是缓存或
启动瞬态造成的。

## 引用时必须带的三条

1. **两边精度不同**：ARM 是 float64，PL 是 Q1.15 定点。和 16.7× 同口径，
   不能写成"同精度下 160×"。
2. **外壳不同**：PL 侧走 AXI DMA（含每块 0.56—0.59 ms 的搬运与驱动），
   演示通路走 I2S 直连、没有这段。所以这个数对演示通路是保守估计。
3. **16.7× 不能丢，也不能和它相乘**：16.7× 是 ARM 对 **HLS 核**
   （`fir_multiband`，36 拍/采样，块模式外壳）；演示通路用的是**手写 RTL 核**
   （3 拍/采样）。从 16.7 到 160 有两个来源：核从 36 拍降到 3 拍，加上外壳从
   "PS 逐块搬运"换成"PS 不在环"。两个数描述两颗不同的核。

## 复现

```bash
# 板上 root，先停服务
systemctl stop fir-pl-webui
pkill -f '[f]ir_pl_live'
cd /home/xilinx/speedup_20261102
XILINX_XRT=/usr /usr/local/share/pynq-venv/bin/python3 -u accel_demo_chain.py --passes 10
```

目录内容：`fir_rtl_dma.bit`（md5 `929ce60f…`）、`fir_rtl_dma.hwh`（`a2b3b387…`）、
`rtl_dma_probe.py`、`accel_demo_chain.py`、`band_design.py`，以及逐位比对用的
`in_q15.mem` / `rtl_fe_out.txt`。旧位流留在同目录的 `fir_rtl_dma_20261025.*`。

重编位流：

```bash
rm -rf build/vivado/fir_rtl_dma
MSYS_NO_PATHCONV=1 cmd /c "E:\Xilinx\Vivado\2020.2\bin\vivado.bat -mode batch \
  -source D:\pynq-multiband-audio-accel\board\overlay\build_rtl_dma.tcl"
```

第一次撞上 `create_bd_design` 的随机 rsb 读失败（`couldn't read file "testbench.tcl"`），
删工程目录重跑一次就过。

测完把演示服务起回来：`systemctl start fir-pl-webui`。

## 原始输出（重编位流，`--passes 10`）

```
输入 144000 采样（3.0 s @ 48000 Hz），11 根谱线 + 底噪，峰值 29.8% FS
  CPU 第 2 趟 0.897 s
  CPU 第 3 趟 0.895 s
  ARM：6.196 µs/采样（892.2 ms 整段）

加载 fir_rtl_dma.bit
  连发 10 × 65535 点，走 DRC 通路（ctrl[0]=1）
  墙钟 0.0256 s，总 655350 采样
  PL 墙钟（含 DMA/驱动）：0.0390 µs/采样 = 25.65 Msamp/s
  PL 核内：0.0300 µs/采样 = 33.33 Msamp/s（3.000 拍/采样 @100 MHz）
  每块 DMA+驱动开销 0.59 ms（65535 点）

== 结果 ==
  乘加次数：两边都是 579 次/采样（3 × 193 抽头）
  ARM 6.196 µs vs PL 墙钟 0.0390 µs  => 158.9 倍
  ARM 6.196 µs vs PL 核内 0.0300 µs  => 206.5 倍（只算核，不含搬运）
  实时预算 20.833 µs/采样：ARM 占 29.7%，PL 核内占 0.1%
```
