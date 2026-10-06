---
name: audio-algorithm-prompts
description: >
  Reusable prompt patterns for FIR design, FFT spectrum analysis, and per-band
  dynamic range compression in this project, each paired with the parameter values that
  must not be copied blindly. Use when asking an assistant to design filters, produce a
  spectrum plot, or implement DRC.
license: MIT
metadata:
  version: "1.0.1"
  updated: "2026-09-25"
---

# 音频处理算法常用提示词工作流

## 什么时候用

- 要让 AI 设计滤波器、出频谱图、实现 DRC
- 要新写一条和本项目参数有关的提示词

## 什么时候别用

- 要的是参数本身 —— 唯一定义处是 src/python/band_design.py
- 只是要改一行代码 —— 直接说就行，不用套提示词

## 修订记录

| 版本 | 日期 | 改动 |
|---|---|---|
| 1.0.0 | 2026-09-25 | 初版 |
| 1.0.1 | 2026-10-02 | 按官方 Agent Skill 的 SKILL.md 写法补 frontmatter 和适用范围 |

## 场景一：设计 FIR 滤波器

提示词：

> 帮我用 Python scipy.signal.firwin 设计一个 193 抽头的 FIR 低通滤波器，
> 截止频率 500 Hz，采样率 48000 Hz，并画出频谱响应。

要点：抽头数（决定阶数）、截止频率、采样率，三个都得说清楚。

**上面那行的数别照抄。** 抽头数和截止频率随设计变，本项目当前是
**193 抽头、边界 500 / 1000 / 2000 Hz**，唯一定义处是 `src/python/band_design.py`。
照抄示例里的旧值（65 抽头 / 600 Hz）会生成和硬件对不上的系数。

另外本项目是**设计 3 个低通、再现场相减**得到频段，不是每段各设计一个带通 ——
提示词里说成「设计带通」，出来的结构对不上。

## 场景二：FFT 频谱分析

提示词：

> 对一段 10 秒的 48 kHz 语音信号做 FFT 分析，只显示 0-2000 Hz 范围，
> 纵坐标归一化到 0-1.5，横坐标显示频率(Hz)。

要点：必须强调去直流 `x = x - np.mean(x)`，否则 0 Hz 会出现巨峰；
幅度要归一化 `/ n * 2`。画多大范围按实际信号定。

## 场景三：动态范围压缩（DRC）

提示词：

> 实现一个助听器场景下的动态范围压缩算法，阈值 0.1、压缩比 0.7，
> 对每个频段独立压缩。

要点：用 `np.where` 判断阈值，超出部分乘压缩比。

**但阈值和压缩比是要量的，不是照抄的。** 出厂默认（0.1 / 0.7）在真实语音上
只压得掉 1.0 dB；本项目实际用的是 0.03 / 0.5，测到 1.60 倍 = −4.1 dB。
这两个是运行时可写的寄存器，改完不用重新综合。
