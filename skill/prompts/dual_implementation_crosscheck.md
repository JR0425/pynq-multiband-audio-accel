---
name: dual-implementation-crosscheck
description: >
  Make two independent implementations of the same algorithm prove each other: same input
  file, same numeric contract, bit-exact compare in one process, plus a pass-through
  identity. Use when one implementation "passes its own tests" and you need evidence that
  is not self-referential — e.g. HLS vs hand-written RTL, or model vs hardware.
license: MIT
compatibility: Any. Requires both implementations to share one numeric contract (bit width + rounding).
metadata:
  version: "1.0.0"
  updated: "2026-11-02"
---

# 两套独立实现互证，而不是一套自证

## 什么时候用

- 要给"功能正确"一个不依赖单一实现的证据
- 两套实现并存（参考模型 / HLS / 手写 RTL），想证明它们真的一样
- 评委或同事问"你怎么知道不是两边一起错"

## 什么时候别用

- 两套实现的**数值契约不同**（精度、舍入、饱和策略）——那时只能报 SNR，
  并且必须写明"两边精度不同"，逐位比对没有意义
- 只有一套实现 —— 那只能用黄金向量，不能叫互证

## 提示词模板

```
【A】<实现 + 精度 + 舍入/饱和策略>
【B】<实现 + 精度 + 舍入/饱和策略>
【共同输入】<文件路径、长度、采样率、系数来源>
【判据】逐位相同（要求：连累加器都比，不只比最终输出）

【要你做的】
1. 先判断 A 与 B 的数值契约是否真的相同。任何一处不同，逐位比对就不成立 —— 先说这个。
2. 设计一个"两边同时错成一样"也会被抓到的对照（直通恒等式 / 极端输入 / 边界饱和）。
3. 给出比对脚本骨架：最佳位移 → 逐位 → 每级中间量 → 0/1 退出码。
4. 说明这个证据**不能**证明什么。
```

## 关键设计：让"同时错成一样"变得不可能

| 手段 | 为什么有效 |
|---|---|
| **直通恒等式** | 相减式滤波器组里，四段之和恒等于延迟 D 拍的输入。这是算式层面的事实，不依赖任何实现 |
| **同一进程内并排跑** | 两套实现先后加载、跑同一份输入，排除"环境不同"这个解释 |
| **逐级比中间量** | 最终输出经过移位与饱和，会把低位差异全部吃掉 |
| **边界输入** | 静音、满量程、单频、阶跃 —— 分别激励不同分支（饱和 / 压缩启动 / 直通） |

## 模型容易错在哪

- 把"逐位相同"直接当成功，不检查**被测功能有没有真的被激励**。
  本项目踩过：一份低电平音源的包络最高值只有阈值的一半，压缩器一次都没启动，
  逐位相同只证明了直通没被弄坏
- 忘了先对齐群延迟就判"不同"，然后把时间花在错误的方向上
- 两边精度不同还坚持逐位比对

## 验收

- 直通恒等式成立；
- 至少一份输入让被测功能**确实工作**（有可观察的增益衰减量），且在那种输入下仍逐位相同；
- 能明确说出"这个证据不能证明什么"（例如：不能证明算法适合这个应用，只能证明两套实现一致）。

## 来源

本项目三实现互证（Python / HLS / RTL），同一进程并排 4800 点 0 点不同；
判据与反例见 `report/design_report.md` §5 与 `data/results/rtl_vs_hls.md`。

## 修订记录

| 版本 | 日期 | 改动 |
|---|---|---|
| 1.0.0 | 2026-11-02 | 从三实现互证流程提炼 |
