# build/ 索引

这里放**构建脚本**和它们吐出来的**综合 / 实现报告**。怎么从零跑到出货位流，看仓库根的
`BUILD_GUIDE.md`；一条命令的入口是仓库根的 `rebuild.sh`。

## 脚本

| 文件 | 作用 |
| --- | --- |
| `hls/run_csim.tcl` | C 仿真。跑完和 `data/results/python_golden.txt` 比对，脚本在 `src/python/compare_golden_vs_hw.py` |
| `hls/run_synth.tcl` | `csynth_design`，出估算的资源与拍数 |
| `hls/run_impl.tcl` | HLS 侧的实现（OOC 布局布线），出真实时序 |
| `hls/run_export_ip.tcl` | 导出 IP 给整机 overlay 用 |
| `hls/run_vivado_impl.tcl` | 在 Vivado 里对整机设计做实现，出位流 |
| `hls/report_alt_clock.tcl` | 换时钟约束重出时序报告（找最高能跑多少 MHz 用的） |
| `hls/report_io_timing.tcl` | IO 时序报告 |
| `hls/directives/` | 32 份 pragma / directive 配置，各版本各一份；换工具版本时改这里 |
| `drc_band_tilt.py`、`drc_diag.py` | 各段 DRC 曲线与频响的诊断脚本（Python，不在 HLS 流程里） |
| `requirements.txt` | 上面两个 Python 脚本的依赖 |

`vivado/` 是空的占位目录，Vivado 工程文件不入库（体量大且可脚本重建）。

## 报告

`hls/reports/<版本>/` 一个版本一个目录，版本号就是优化轨迹；
`data/results/hls_synth_metrics.md` 由 `src/python/parse_hls_csynth.py` 从这里自动汇总，
不要手改那份汇总。

**出货配置是 `v25_env_indep`（csynth）和 `v25_env_indep_impl`（布局布线）**，其余是
被替换掉的历史版本。整机 overlay 自己的时序与资源报告不在本目录，在
`board/overlay/fir_timing.rpt` 和 `board/overlay/fir_util.rpt`。

| 目录后缀 | 里面是什么 |
| --- | --- |
| `<版本>/` | `csynth.rpt`（完整 csynth 报告）+ `fir_multiband_csynth.rpt`（同名简版），少数版本另有 `wdrc_csynth.rpt` / `band_proc_csynth.rpt` |
| `<版本>_impl/` | `timing_synth.rpt`、`timing_impl.rpt`、`timing_impl_worst_paths.rpt`、`utilization_synth.rpt`、`utilization_impl.rpt` 及两份分级 `_hier` 版 |
| `*_clk{75,80,85,90,100}MHz/` | 同一设计在不同时钟约束下的时序，用来判断余量 |

关键几版：`v0_baseline`（起点，1661 拍）、`v13_sub_n193`（早期展示材料里那张资源图的出处，449 拍）、
`v19_true_baseline`（31 拍，加速比 19.5× 那趟跑的就是这一版）、`v21_wdrc`（49 拍，加了
WDRC）、`v25_env_indep`（36 拍，出货）。

`v0_baseline` / `v1_partition` 和 `t1_*` / `e1_*` / `e2_*` / `win_shiftchain` 这些是内核源码
重构**之前**的探索实验，记录的是「哪条路走不通」，不能拿当前源码重跑。原因见
`data/results/hls_synth_metrics.md` 开头那段。

## 数字对不上时查哪里

- 资源和拍数：`data/results/impl_metrics.md`（实测）和 `hls_synth_metrics.md`（估算）
- 加速比：`data/results/accel_cpu_vs_fpga.md`
- 时序验收：`board/overlay/fir_timing.rpt`（出货位流的 WNS +0.533 ns、0 条失败端点）
- DMA / 全 PL 直连那五颗位流（`fir_dma*` / `fir_pl` / `i2sdbg`）的结构、延迟和适用范围：
  `data/results/dma_pl_direct_20261025.md`；它们的构建脚本不在本目录，在 `board/overlay/`
