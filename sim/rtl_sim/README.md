# sim/rtl_sim —— 自研 RTL 的仿真、逐位对拍与板上黄金向量

对应源码在 `src/rtl/`：

| 模块 | 作用 |
| --- | --- |
| `fir_lp.v` | 单个 195 抽头低通。历史版本，前端取代了它，逐抽头对系数表那一环还在用 |
| `fir_mb_fe.v` | 多频段前端：3 个低通轮转 + 相减 + 4 段综合 |
| `fir_drc4.v` | 四段各一套「验配增益 + 包络压缩」，定点契约照 `src/hls/fir_types.h` |
| `fir_rtl_shell.v` | 块模式外壳：AXI-Lite 寄存器 + 双口 BRAM，LOAD / RUN / DRAIN 三段 |
| `fir_stream_shell.v` | 流式外壳：AXI-Stream 从口到主口，内部只做 valid/ready，没有 AXI 逻辑 |
| `fir_axis_monitor.v` | 直通监视器，在 DMA 与外壳之间各串一个，供抓取器采样 |
| `fir_wave_cap.v` | 自写抓取器：每拍一条 32 位记录，输入输出各 1024 字 BRAM，PS 经 AXI 读回 |
| `i2s_duplex.v` | 自写 I2S 主端：自己产生 bclk / lrclk，收发两侧各接一个轴 FIFO。后面四颗实验位流的共同底座 |
| `axis_fifo.v` | 上面那个 FIFO，读写两侧各自复位 |
| `fir_pl_direct.v` | 全 PL 直连顶：I2S 收 → 前端 → 压缩 → I2S 发，音频一个字节都不出 PL |
| `i2s_duplex_dbg.v` | 诊断版：在 `i2s_duplex` 里塞进内置 440 Hz 音源、片内自收和翻转计数，出 `i2sdbg.bit` |

前四个的上板结果在 `data/results/rtl_vs_hls.md` §9；后两个只用于出时序图，
**不出任何数字**（监视器让组合路径多一级）。最后四个和 `fir_pl.bit` / `fir_dma*.bit` /
`i2sdbg.bit` 五颗位流是一组，数字与口径见 `data/results/dma_pl_direct_20261025.md`。

## 跑之前

```bash
python tools/gen_rtl_coeffs.py     # 生成 src/rtl/*.mem 和 sim/rtl_sim/in_q15.mem
python tools/ref_model_int.py      # 整数参考模型 → ref_acc_* / ref_out_*
```

第二条对多频段那一半要换成 `python tools/ref_model_mb.py`。

## 跑仿真

```bash
bash sim/rtl_sim/run_xsim.sh                 # tb_fir_lp.v，三个低通各跑一遍
bash sim/rtl_sim/run_xsim_mb.sh              # tb_fir_mb_fe.v，多频段前端
bash sim/rtl_sim/run_xsim_shell.sh           # tb_fir_rtl_shell.v，块模式外壳，1000 点
bash sim/rtl_sim/run_xsim_drc4.sh            # tb_fir_drc4.v，只测压缩单元
bash sim/rtl_sim/run_xsim_stream.sh          # tb_fir_stream_shell.v，AXI-Stream 外壳
bash sim/rtl_sim/run_xsim_long.sh 4800       # tb_fir_rtl_shell.v，长块，出板上黄金向量
bash sim/rtl_sim/run_xsim_wave_cap.sh        # tb_wave_cap.v，抓取器位序
bash sim/rtl_sim/run_xsim_duplex.sh          # tb_i2s_duplex.v，自写 I2S 收发，判 PASS/FAIL
bash sim/rtl_sim/run_xsim_pl.sh              # tb_fir_pl_direct.v，诊断台，只打内部相位快照
```

`run_xsim_duplex.sh` 是那四颗 DMA / PL 直连位流的第一道门：同一个录音 FIFO 灌 20 帧，
收侧把 `sdata_o` 解回来，判逐字相同、TLAST 落在每帧第二个字、左右声道各自解对、
ovf/und 一直是 0。**改过 `i2s_duplex.v` 或 `axis_fifo.v` 先跑这一条再上板。**

`run_xsim_pl.sh` **不出 PASS/FAIL**，它是台诊断台：把板上 2026-10-25 观察到的时序
（flush 20 ms 后立刻放行，数据不出；中间停一下再放行就正常）原样喂进去，按秒打一次
`ph` / `v_sr` / 两个 FIFO 水位 / 各级握手计数。判读方法是看 `run+20us` 之后
`out_vld` 和 `fifo_wr` 还涨不涨，不涨的话 `ph` 指到哪一级就是哪一级卡住。
它需要 `fir_mb_fe` 的系数表，脚本会把 `coeffs_lp*_n193.mem` 拷进工作目录。

`fir_lp` 分三次跑而不是在一个 testbench 里例化三次，是因为系数表按文件名读，
三份一起例化会抢同一个文件。三个低通的截止频率分别是 500 / 1000 / 2000 Hz。
其余几份都只认固定的相对文件名（`in_q15.mem`、`coeffs.mem`），同样一次跑一个。

`run_xsim_long.sh` 的参数：

```bash
run_xsim_long.sh <点数> [音源 wav 绝对路径] [起始偏移] [黄金目录后缀]
```

- 不给音源就用 `in_q15.mem`，黄金目录是 `golden_n4800/`。
- `DRC=1` 环境变量打开压缩路径。
- 第 4 个参数用来并存不同音源，例如
  `DRC=1 bash run_xsim_long.sh 4800 <abs>/ab_board_in.wav 62131 _loud`
  写进 `golden_n4800_drc_loud/`。音源必须给绝对路径 —— 脚本会先 cd 进工作目录。

## 比对

```bash
python sim/rtl_sim/check.py        # fir_lp：累加器 + 输出，逐位
python sim/rtl_sim/check_mb.py     # fir_mb_fe：9 路中间量 + 输出，逐位
```

`ref_*` 是参考模型落的盘，`rtl_*` 是仿真输出的盘。比对先比 40 位累加器再比 int16
输出 —— 输出是「右移 17 + 饱和」之后的，低 17 位的信息已经丢掉，
只看输出分不清「乘法错一点」和「移位量写错」。

外壳那几份的对拍直接写在 testbench 里（跑完自己读 `ref_*` 比），没有单独的
check 脚本；不一致时返回非 0。

## 黄金向量（板上对拍用）

| 目录 | 音源 | 模式 | 用途 |
| --- | --- | --- | --- |
| `golden_n4800/` | `in_q15.mem`（noisy_voice） | 仅前端 | 块模式与 DMA 版的 4800 点逐位基准 |
| `golden_n4800_drc_loud/` | `ab_board_in.wav` 偏移 62131 | 压缩 | 压缩器**真的启动**的那份；`quiet` 那份门限都没进，测不出错 |
| `golden_n4800_drc_quiet/` | noisy_voice | 压缩 | 只证明直通没坏、定点取整对 |

每个目录里是 `in_q15.mem` + `rtl_fe_out.txt`（仿真出来的期望输出）。板上探针
`board/scripts/rtl_*.py` 从同目录读这两个文件。**别用 `quiet` 那份判压缩对不对**：
包络没进门限，8 级流水写错两处时它照样 1000/1000 全对。

## 结果

逐位全等，结论和范围说明在 `data/results/rtl_vs_hls.md`。

## 其它文件

| 文件 | 作用 |
| --- | --- |
| `synth_mb.tcl`、`impl_mb.tcl` | 多频段前端的 OOC 综合与布局布线（时序/资源数字的来源） |
| `tb_fir_lp.v`、`tb_fir_mb_fe.v`、`tb_fir_drc4.v`、`tb_fir_rtl_shell.v`、`tb_fir_stream_shell.v`、`tb_wave_cap.v`、`tb_i2s_duplex.v`、`tb_fir_pl_direct.v` | 对应上面各条 `run_*` 脚本的测试台 |
| `ref_f32_lp*.txt` | 浮点参考（用来看定点化损失了多少，不参与逐位比对） |
| `ref_out_lp*.txt` / `rtl_out_lp*.txt` | 上面的输出比对文件 |
| `xsim_work*/` | xsim 工作目录，已进 `.gitignore`，删了会重生成 |

整机（PS7 + 互连 + 外壳 + 前端 + DRC）的构建不在这里，走 `board/overlay/build_rtl*.tcl`。
