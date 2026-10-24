# sim/rtl_sim —— 自研 RTL 的仿真与逐位对拍

对应源码在 `src/rtl/`：`fir_lp.v`（单个低通）和 `fir_mb_fe.v`（多频段前端：
3 个低通轮转 + 相减 + 4 段综合）。两块都**没有** DRC、每段增益和 AXI 外壳。

## 跑之前

```bash
python tools/gen_rtl_coeffs.py     # 生成 src/rtl/*.mem 和 sim/rtl_sim/in_q15.mem
python tools/ref_model_int.py      # 整数参考模型 → ref_acc_* / ref_out_*
```

第二条对多频段那一半要换成 `python tools/ref_model_mb.py`。

## 跑仿真

```bash
bash sim/rtl_sim/run_xsim.sh       # tb_fir_lp.v，三个低通各跑一遍
bash sim/rtl_sim/run_xsim_mb.sh    # tb_fir_mb_fe.v，多频段前端
```

`fir_lp` 分三次跑而不是在一个 testbench 里例化三次，是因为系数表按文件名读，
三份一起例化会抢同一个文件。三个低通的截止频率分别是 500 / 1000 / 2000 Hz。

## 比对

```bash
python sim/rtl_sim/check.py        # fir_lp：累加器 + 输出，逐位
python sim/rtl_sim/check_mb.py     # fir_mb_fe：9 路中间量 + 输出，逐位
```

`ref_*` 是参考模型落的盘，`rtl_*` 是仿真输出的盘。比对先比 40 位累加器再比 int16
输出 —— 输出是「右移 17 + 饱和」之后的，低 17 位的信息已经丢掉，
只看输出分不清「乘法错一点」和「移位量写错」。

## 结果

逐位全等，结论和范围说明在 `data/results/rtl_vs_hls.md`。

## 其它文件

| 文件 | 作用 |
| --- | --- |
| `synth_mb.tcl`、`impl_mb.tcl` | 多频段前端的综合与实现脚本 |
| `ref_f32_lp*.txt` | 浮点参考（用来看定点化损失了多少，不参与逐位比对） |
| `ref_out_lp*.txt` / `rtl_out_lp*.txt` | 上面的输出比对文件 |
