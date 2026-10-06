---
name: audio-processing-bugs
description: >
  Three recurring Python audio-processing bugs: DC offset spiking the 0 Hz bin,
  slicing a variable for a plot and truncating the saved file, and normalising an FFT
  the wrong way. Use when a spectrum shows a huge spike at 0 Hz, when the output wav
  length does not match the input, or when a figure that should show two curves appears
  to show one.
license: MIT
metadata:
  version: "1.0.1"
  updated: "2026-09-25"
---

# Python 音频处理常见 Bug 避坑指南

## 什么时候用

- 频谱在 0 Hz 处出现一根巨峰，把正常频段压没了
- 输出音频的时长和输入对不上
- 图里画了两条线，看起来只有一条

## 什么时候别用

- 问题出在硬件通路上 —— 那看 pynq_audio_playback.md
- 频谱已经用 Welch 平均过还是这样 —— 多半不是这一页的原因

## 修订记录

| 版本 | 日期 | 改动 |
|---|---|---|
| 1.0.0 | 2026-09-25 | 初版 |
| 1.0.1 | 2026-10-02 | 按官方 Agent Skill 的 SKILL.md 写法补 frontmatter 和适用范围 |

## 症状

1. 频谱图在 0 Hz 处出现一根巨峰，把正常频段全压没了
2. 生成的音频时长和原始文件对不上（比如 10 秒变 2 秒）
3. 图里两条线完全重叠，看起来只有一条

## 原因和解决

**1. 直流偏移。** 音频数据有极微小的正负不平衡，FFT 会在 0 Hz 上把它堆成一根巨峰。

算频谱和存文件之前先减均值：

```python
x = x - np.mean(x)
```

**2. 画图时切片覆盖了原始变量。** `x = x[:n]` 看着只是取一段来画，
实际上把 `x` 本身截断了，后面写文件写的就是截断后的数据。

要截就换一个新变量：

```python
x_view = x[:n]        # 对
x = x[:n]             # 错 —— 后面的写文件也跟着被截
```

**3. 只喂了一个声道。** 素材左声道 440 Hz、右声道 880 Hz 时，只取左声道
会让 880 Hz 那一路整个消失，滤波效果看不出差别。

**4. FFT 幅度没归一化、坐标轴没限范围。** 幅度用 `/ n * 2`，否则 Y 轴会出现
不合理的高值。画频谱时先 `plt.xlim` / `plt.ylim` 聚焦要看的那一段 ——
但范围按实际信号定，别照抄。

本项目的基线脚本 `src/python/multiband_baseline.py` 这几条都已经处理过。
这篇留着是因为新写一个分析脚本时还会再犯。
