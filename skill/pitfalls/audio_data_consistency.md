---
name: audio-data-consistency
description: >
  Keep filter coefficients and audio data on the same sample rate, and make sure
  a hardware-versus-software comparison actually compares the two things it claims to.
  Use when board output and the Python baseline each look fine on their own but do not
  match, when adding an audio source whose sample rate differs from the design, or when
  writing a comparison script and the question is what its two ends should be.
license: MIT
metadata:
  version: "1.0.1"
  updated: "2026-09-28"
---

# 系数和数据必须同一个采样率（否则软硬件对撞是假的）

实测：2026-09-28。

## 什么时候用

- 板子输出和 Python 基线「各自看着都对」，一比就是不像
- 要新接一个音频源，采样率和设计不一样
- 要写一个「硬件 vs 参考」的比对脚本，得先定两端的对象

## 什么时候别用

- 只是浮点误差级别的对不上 —— 那种看 SNR 就够
- 两边已经确定用的是同一套系数、同一个采样率

## 修订记录

| 版本 | 日期 | 改动 |
|---|---|---|
| 1.0.0 | 2026-09-28 | 初版 |
| 1.0.1 | 2026-10-02 | 按官方 Agent Skill 的 SKILL.md 写法补 frontmatter 和适用范围 |

## 症状

拿板子上的输出和 Python 基线对撞，两边"看起来都在正常工作"，
但逐点比对根本对不上 —— 不是浮点误差那种对不上，是形状都不像。

## 原因

`src/python/export_coefficients.py` 设计滤波器系数时写死了 `fs = 48000`：

```python
fs = 48000
numtaps = 65
bands = [(0, 300), (300, 600), (600, 1000), (1000, 8000)]
signal.firwin(numtaps, cutoff, fs=fs, pass_zero=...)
```

而 `src/python/multiband_baseline.py` 里的 `fs` 是从 wav 文件头读出来的：

```python
data, fs = sf.read(audio_path)   # fs 跟着文件走
```

测试音频 `data/audio/real_voice.wav` 实测是 **44100 Hz**。

系数是"按 48 kHz 设计的"，基线的滤波器却是"按 44.1 kHz 设计的" ——
同一个 `firwin` 调用，`fs` 不同，归一化频率就不同，**算出来的 65 个抽头不是同一组数**。
两组不同的滤波器，输出自然对不上。

**哪边是对的：板载 codec 是 48 kHz（RTL 里写死），所以按 48 kHz 设计的那组系数才是硬件真正在用的那组。**

## 解决

黄金参考不要用 `multiband_baseline.py` 现算，而是**用和硬件完全相同的那组系数**去算。
`src/python/export_golden.py` 就是干这个的：它读 `sim/hls_csim/lp_*.txt` ——
和硬件 `fir_coeffs_n<N>.h` 是**同一批**文件（两者都出自
`src/python/export_coefficients.py`，而系数只在 `band_design.py` 里设计一次），
再对同一段输入做卷积。

> 补记（2026-09-30）：本文写的时候系数是 `sim/hls_csim/fir_coeffs_{1..4}.txt`
> 那四份（每段一个带通）。后来频段划分改成相减式，系数也换成
> `lp_<截止>_n<抽头>.txt`（三个低通），文件名不再是那四个了。
>
> 而且这个坑**已经从根上堵掉**：采样率 `FS`、边界、设计法现在都只在
> `band_design.py` 里定义一次，`multiband_baseline.py` 也改成先
> `resample_poly` 到 48 kHz 再处理 —— 两边不会再各拿一个 `fs`。
> 留着这篇是因为「同一个 firwin、fs 不同 → 系数不是同一组数」这件事本身
> 不容易想到，以后接别的东西（比如换采样率的音频源）还会再撞上。

判定标准（`src/python/compare_golden_vs_hw.py`）：相关系数 ≥ 0.999、
最大单点误差 ≤ 1e-3，且要扫描 ±16 个采样的位移之后再比。

## 顺带一条：比对脚本的另一端必须是"同一套系数下的输出"

同一天还翻出 `src/python/compare_results.py` 比错了对象：

- 它把 `data/audio/test_input.txt`（**输入**）当成"Python 基准"
- 去和 `hw_output.txt`（**输出**）比
- 而当时的 `hw_output.txt` 是 `generate_mock_hw_output.py` 造的假数据（输入加一点噪声）

输入和"输入+噪声"的相关系数当然接近 1，脚本会照样打出
「✅ 结论：硬件输出与 Python 基准高度一致」。

**这两样一比，什么都没证明。** 脚本原注释里就写着「目前用测试数据占位」——
危险的地方在于**它长得像一次通过**。

改成 `compare_golden_vs_hw.py`：一端是真黄金参考（同一套系数算出来的），
一端是真硬件输出，两边对不上就红。

## 相关

- Vitis HLS 仿真环境：`skill/pitfalls/hls_csim_setup.md`
- 综合与优化指令：`skill/pitfalls/hls_synthesis_and_directives.md`
