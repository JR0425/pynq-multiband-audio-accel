---
name: latency-budget-decomposition
description: >
  Report end-to-end latency as a decomposed budget instead of one summed number: each
  component gets its own measurement, its own unit and its own confidence level. Use when
  someone asks "what is your end-to-end latency", or when a total was obtained by adding
  numbers that were measured under different conditions.
license: MIT
compatibility: Any real-time system. The specific decomposition is audio I2S; the method is general.
metadata:
  version: "1.0.0"
  updated: "2026-11-02"
---

# 端到端延时：报分解，不报合数

## 什么时候用

- 要回答"端到端延时多少"，而你手上的数字是几个不同条件下分别量的
- 有人（或你自己）把组件延时相加当作实测值
- 想避免"看起来精确、其实一半是推算"的数字

## 什么时候别用

- 做过一次真正的合并实测（声学激励 → 输出拾音）—— 那就可以报单一数，但仍要给出方法与重复次数

## 提示词模板

```
【我的系统】<信号链，逐级列出>
【各级已有的数】<数值 + 单位 + 怎么得到的（实测/推算/来自手册）>
【我打算报的数】<合数>

【要你做的】
1. 逐级判断每个数是「实测」「推算」还是「假设」，把三者分开列；
2. 指出相加过程中容易重复计算或漏算的项（例如群延迟与缓冲延迟是否重叠）；
3. 给一个**只测总延时**的实验设计（不需要知道内部结构就能做的那种）；
4. 明确写出：在没做第 3 步之前，这个合数该怎么措辞才不算撒谎。
```

## 措辞模板（照抄）

> 数字段延时 **X ms**（由 N 拍群延迟定，48 kHz 下可算），
> 声学段 **Y ms**（2026-MM-DD 实测，方法见 xxx），
> **两个分量分报，尚没有一次合并的声学实测**。

这句话比"端到端 X+Y ms"诚实，也比"大约 X ms"信息量大。

## 模型容易错在哪

- 把推算写成实测。本项目曾写"全 PL 直连按各级缓冲推算约 2.3 ms"，
  后来发现**公式里的群延迟项还少算了一半** —— 推算一旦被当成结论，错误会被传播
- 相加时重复计入同一段延迟（群延迟与缓冲延迟在链路上可能是同一段时间）
- 用"理论最小"（纯拍数）代表实测延时，忽略软件轮询、调度与 IO

## 验收

- 每个分量都有自己的出处，且出处写在旁边；
- 能量出"总延时"的方法已经写出（哪怕还没做）；
- 措辞不把推算混进实测。

## 来源

本项目 `data/results/dma_pl_direct_20261025.md`（四条通路的延时口径分表记，不合并成"最快延时"）
与 `report/design_report.md` §0 / §10 的写法。

## 修订记录

| 版本 | 日期 | 改动 |
|---|---|---|
| 1.0.0 | 2026-11-02 | 从"2.3 ms 是推算不是实测"那次修正提炼 |
