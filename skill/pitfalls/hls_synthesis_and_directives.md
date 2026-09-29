# Vitis HLS 综合与优化指令：哪把闸真的管用

实测：Vitis HLS 2020.2，目标 `xc7z020clg400-1`（PYNQ-Z2），10 ns 时钟（100 MHz），2026-09-28 ~ 09-29。

## 一、先看结论

| 写法 | 管用吗 | 说明 |
|---|---|---|
| `set_directive_allocation -limit N -type operation ... fmul` | ✅ **最有效** | 限死**浮点**乘法器个数，是控制面积的主闸门 |
| `set_directive_allocation -limit N -type operation ... mul` | ✅ **最有效** | 同上，但限的是**定点/整数**乘法器。⚠️ 两个名字不能混用：从浮点版把 `fmul` 抄到定点版会**完全不生效、也不报错**（详见 `hls_fixed_point_types.md`） |
| `set_directive_array_partition` | ✅ 管用 | 但和源码里的 `#pragma` 冲突会直接报错（见下） |
| `set_directive_unroll` | ❌ **不可靠** | 对"循环里还套着循环"的那种，静默失效 |
| 源码里的 `#pragma HLS UNROLL` | ✅ 管用 | 前提是写对位置（写在真的需要展开的那个循环上） |

## 二、指令写在 Tcl 里，不要写进源码

指令（directives）单独放 `build/hls/directives/<版本>.tcl`，
源码 `src/hls/fir_multiband.cpp` 保持干净。

理由：**源码是已经 csim 验过的那一份**。把 pragma 写进源码，就等于"验过的代码"和"综合的代码"
不是同一份了，出了差别得逐个 pragma 试回去。指令留在 Tcl 里，
同一份源码配不同 tcl 就能综合出十几个版本做对比。

代价：`build/hls/reports/<版本>/` 下每份报告各自对应一份 tcl，
要复现必须**tcl + 编译开关**一起给（开关见各 tcl 文件开头）。

## 三、`set_directive_unroll` 会静默失效

对下面这种"外层循环里套着内层循环"的结构：

```c
for (int k = 0; k < N_TAPS; k += FIR_PARTIAL) {
    for (int p = 0; p < FIR_PARTIAL; p++) { ... }
}
```

在 tcl 里写 `set_directive_unroll -factor 5` 指向**外层** k 循环 —— 不管用。
日志里只有一行：

```
WARNING: [HLS 200-960] Cannot flatten loop 'VITIS_LOOP_xx' ...
```

**它不报错，只是不做。** 实测 `v3b_unroll_inner` 停在 933 拍、5 DSP，
和没加指令时一模一样 —— 说明指令根本没生效。
（对照：把 `#pragma HLS UNROLL` 直接写进源码的 `v3b_limit_mul`，327 拍、27 DSP。）

**判断办法**：加完指令重跑，看报告里的循环表 —— 目标循环的 `Pipelined` 列还是 `no`、
`II` 还是大于 1，就说明没生效。别只看命令有没有报错。

## 四、Tcl 指令和源码 pragma 不能同时作用在同一个数组上

`hist` 数组在源码里已经有：

```c
#pragma HLS ARRAY_PARTITION variable=hist cyclic factor=5 dim=1
```

tcl 里再写一条 `set_directive_array_partition -variable hist ...` 就会：

```
ERROR: [HLS 200-904] Found conflicting partition directives
```

**要么改源码，要么把 tcl 那条注释掉。** 9/29 试"把 hist 拆得更开"时，
改成在源码里用条件编译给出几种写法，靠 `-DFIR_HIST_CYC13` / `-DFIR_HIST_FULL` 切换。

## 五、pragma 里写不了宏

HLS 的 pragma 解析器**不展开 C 宏**，所以：

```c
#pragma HLS ARRAY_PARTITION variable=hist cyclic factor=FIR_PARTIAL   // ✗ 认不出来
#pragma HLS ARRAY_PARTITION variable=hist cyclic factor=5             // ✓ 只能写死
```

但 `#if` 是预处理器干的，**它认宏**。所以切换 pragma 变体的写法是：

```c
#if defined(FIR_HIST_FULL)
#pragma HLS ARRAY_PARTITION variable=hist complete dim=1
#elif defined(FIR_HIST_CYC13)
#pragma HLS ARRAY_PARTITION variable=hist cyclic factor=13 dim=1
#else
#pragma HLS ARRAY_PARTITION variable=hist cyclic factor=5 dim=1
#endif
```

## 六、这次量出来的东西

复现命令见 `build/hls/run_synth.tcl` 文件头；完整表格见 `data/results/hls_synth_metrics.md`。

**实时预算**：48 kHz、每采样 20.83 µs、100 MHz → **2083 拍/采样的上限**。

| 版本 | 拍/采样 | DSP | LUT |
|---|---|---|---|
| `v0_baseline` 单一累加器 | 1661 | 5 | 1772 (3.3%) |
| `v1_partition` 把能拆的都拆了 | 1805 | 5 | 17239 (32.4%) |
| `v2_struct_only` 改结构、不加 pragma | 824 | 5 | 3347 (6.3%) |
| `v3b_limit_mul` 改结构 + 限乘法器 5 个 | **327** | 27 | 9936 (18.7%) |
| `e1_pragma_unroll` 全展开 | 226 | **345** | 52437 (98.6%) |

三条能带走的东西：

**1. 瓶颈不在内存带宽。** `v1_partition` 把数组全拆成了寄存器，
LUT 涨了 10 倍（1772 → 17239），拍数反而**变多了**（1661 → 1805）。
说明读口不够不是问题 —— 白花了 32% 的 LUT。

**2. 真正的瓶颈是浮点加法的延迟链。** `y += ...` 只有一个累加器时，
浮点加法电路（`fadd_32ns_32ns_32_5_full_dsp_1`）自身 5 拍延迟，
y 又必须等上一轮的 y，于是 II=6，每 6 拍才喂得进一个抽头。
拆成 5 个部分和（`FIR_PARTIAL=5`）把这条链打断 —— **光改结构就 2.02 倍，一个 DSP 都没多花**（`v2_struct_only`）。

**3. 面积是靠 `allocation` 限乘法器个数调的，不是靠展开。** 全展开（226 拍）要 345 个 DSP，
**板上只有 220 个，装不下**。用 `set_directive_allocation -limit N` 卡住乘法器数量，
N 越小越省面积、拍数越多：

| 限流 | 拍/采样 | DSP | LUT |
|---|---|---|---|
| 5 | 327 | 27 | 9936 (18.7%) |
| 10 | 299 | 52 | 14699 (27.6%) |
| 20 | 287 | 96 | 21024 (39.5%) |
| 40 | 280 | 192 | 34479 (64.8%) |

从 5 到 10 只快了 8.6%，DSP 却翻了近一倍 —— **拐点在 5 附近，再往上不划算**。
`v3b_limit_mul`（327 拍 / 27 DSP / 18.7% LUT，5.08 倍）是这块板子上比较划算的一档。

## 七、一次不划算的尝试：系数对称折叠

四组系数都是严格对称的（`c[k] == c[N_TAPS-1-k]`，逐个核对过，不对称量为 0），
因为 `firwin` 生成的线性相位 FIR 必然对称。所以：

```
c[k]*h[k] + c[64-k]*h[64-k]  ==  c[k] * ( h[k] + h[64-k] )
```

先把两个采样加起来、再只乘一次，65 个抽头 → 33 次乘法。理论上该省一半 DSP。

**实测不划算：**

| 版本 | 拍/采样 | DSP | LUT |
|---|---|---|---|
| `v4_ref_limit5` 直算（对照） | 327 | 27 | 9936 |
| `v4_fold_limit5` 折叠 | **1247** | 25 | 8609 |
| `v4_fold_limit10` 折叠、乘法器放到 10 | **1247** | 42 | 10695 |
| `v4_fold_histfull` 折叠 + hist 全拆开 | 663 | 79 | 19924 |

原因：倍数确实减半了（乘法 65 → 33 次），但**加法从 65 次变成 66 次**
（每对多一次先加）。而这条线的瓶颈正是加法延迟链、乘法靠限流就能复用 ——
**折叠减掉的是不紧张的资源，加的却恰恰是最紧张的那个。**

旁证：把乘法器上限从 5 提到 10，拍数**一动不动**（都是 1247）。
如果乘法器数量是约束，放宽它一定会变快；没变，说明它压根不是约束。

结论：这条路在这套结构下走不通。留着的价值是"试过、量过、知道为什么不划算"——
**等以后定点化（`ap_fixed`）让加法变便宜了，值得再试一次。**

## 相关

- 仿真环境：`skill/pitfalls/hls_csim_setup.md`
- 系数与数据的采样率：`skill/pitfalls/audio_data_consistency.md`
- 报告生成脚本：`src/python/parse_hls_csynth.py`
