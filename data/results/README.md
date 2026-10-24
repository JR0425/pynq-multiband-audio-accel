# data/results 索引

本目录是**所有实测读数**的落盘处。文件名带日期，代表测的那天；同一件事可能测过好几次，
**以本文件「当前结论」那一栏指定的文件为准**，其余是历史或被推翻的记录。

出货配置固定为：位流 `v25_env_indep`（36 拍/样本、205/220 DSP、WNS +0.533 ns、
md5 `5aa079f29e8d66c21d1d34f1302c3695`），演示档 16.2 ms（L=360 / NBUF=2）。
凡是版本号写 `v18`/`v19`/`v20`/`v21` 的读数都不是出货版的数。

## 当前结论

| 问题 | 文件 | 结论 |
| --- | --- | --- |
| 加速比多少 | `accel_cpu_vs_fpga.md` | **16.7×**（10-23 实测，ARM 6.117 µs/样本 vs 核 0.367 µs/样本）；引用必须带四条限定 |
| 核单独 / 整机 overlay 的资源与时序 | `impl_metrics.md` | 布局布线后的实测；`hls_synth_metrics.md` 是同一批的 csynth 估算值，两者不要混用 |
| 三个延迟档位的数字延迟 | `latency_retest_20261023.md` | 36 拍核复测；验配界面的端到端延迟另见 `tuning_ui_latency_20261018.md` |
| 实时通路会不会丢块 | `live_dropout_recheck_20261024.md` | 16.2 ms 档 600 秒 0 丢块；10-23 那次「两档都在丢」是探针插桩自己造成的 |
| 长时间跑会不会挂 | `soak600_20261023.md` | 两趟 600 秒不挂（36 拍核、演示同配置）；原始转储在 `soak600_20261023/` |
| 压缩特性对不对 | `wdrc_characteristic.md` | 板上实测的 WDRC 输入-输出曲线 |
| 异常输入会不会崩 | `robustness.md` | 10-23 在出货版上实测 |
| 四件事一起测（延迟/CPU/功耗/质量） | `performance_profile_20261022.md` | 10-22 的性能线汇总（当时是 49 拍核，延迟与 CPU 两项需以 10-23 复测为准） |
| Python 基线的数 | `baseline_metrics.md` | 48 kHz / 4 段 / 193 抽头 |
| 自研 RTL 和 HLS 版的数值关系 | `rtl_vs_hls.md` | 逐位对拍；RTL 只有 3 个低通，无相减 / DRC / AXI 外壳 |

## 子目录

| 目录 | 内容 |
| --- | --- |
| `drop_recheck_20261024/` | 10-24 丢块复查的 29 份原始产物（gzip）。`*_notrace` = 关插桩，`*_trace*` = 开插桩 |
| `soak600_20261023/` | 10-23 两趟 600 秒长跑的原始 jsonl 与转储，汇总见 `soak600_20261023/table.md` |

## 原始读数（不是结论，别直接引用）

| 类 | 文件 |
| --- | --- |
| 加速比跑测 | `compare_cpu_fpga_run_20261023.txt`（16.7×，出货版）、`compare_cpu_fpga_run_20261022.txt`（12.5×，v21/49 拍）、`compare_cpu_fpga_run_20261008.txt`（19.5×，v19/31 拍）、`compare_cpu_fpga_run_20261001_fix.txt` |
| 实时通路运行 | `fir_live_run_20261015.txt`、`fir_gain_ab_run_20261015.txt`、`fir_audio_loop_run_20261006.txt`、`fir_audio_loop_run_20261001.txt`、`fir_wdrc_ab_run_20261019.txt` |
| 延迟 / 质量 / 鲁棒性 | `latency_n2_*_20261023.json`、`latency_n3_l480_20261023.json`、`quality_metrics_20261022.txt`、`robustness_20261023.json`、`robustness_run_20261023.txt`、`wdrc_curve_20261023.json`、`wdrc_curve_run_20261023.txt`、`acoustic_lat_20261022.npz`、`four_band_response_20261022.csv`、`cpu_20261022.csv` |
| HLS / RTL 比对 | `python_golden.txt`、`hw_output.txt`、`hw_transparent_i16.txt`、`v25_output.txt`、`v25_transparent.txt`、`v25_wdrc_output.txt`、`fixed_dw16_cw*.txt`、`regress_float.txt`、`rtl_fe_*.rpt` |
| 旧版本核的输出 | `v18_axi_shell_output.txt`、`v20_baseline_output.txt`、`v20_baseline_transparent.txt`、`v21_wdrc_output.txt`、`v21_transparent.txt`、`v21_legacy_output.txt` |

## 已知不能引用的记录

| 文件 | 为什么 |
| --- | --- |
| `mic_input_selfosc_run_20261010.txt`、`mic_input_selfosc_scan_20261013.txt`、`mic_selfosc_probe3~10_log_20261013.txt`、`live_run_with_probe7_fix_20261013.txt` | 10-13 判定的「话筒满幅自激」**是错的**：低 24 位补码没做符号扩展，数字零被读成 rms 46341（= 65535/√2）。保留是为了记这次误判，不能当故障证据 |
| `reference_overlay_metrics.md` | 开源参考 overlay（`soheilbh/fir_accel_pynq`）的数据，和自研核无关 |
| `board_hang_evidence_20261016.md`、`board_hang_evidence_20261017.md` 及 `board_hang_rcu_stall_dump_*` | 板子死机调查的现场账本。里面「关实时就能救」的结论**已被推翻**，读之前先看 10-17 那份的最终判断 |
