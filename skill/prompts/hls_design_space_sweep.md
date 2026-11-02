---
name: hls-design-space-sweep
description: >
  Turn "which configuration should we pick" into a machine-generated comparison table
  instead of a hand-copied one: enumerate variants, run csynth on each, extract the same
  fields, then read the trade-off off the non-dominated front. Use when you have several
  candidate directives/tap counts and need an optimise-before/after table.
license: MIT
compatibility: Vitis HLS 2020.2+. Field names in the report vary across versions.
metadata:
  version: "1.0.0"
  updated: "2026-11-02"
---

# 把"选哪一档"做成一张自动生成的表

## 什么时候用

- 要交"优化前后的性能与资源对比表"
- 有几个候选配置（抽头数 / 并行度 / 数据类型 / 限流数），说不清该选哪个
- 有人质疑某个资源数字，需要现场复核

## 什么时候别用

- 只有一两个候选 —— 手抄更快
- 需要的是**实测**值（这时要用布局布线报告；csynth 的估算会高估 LUT 约 2.5 倍，
  DSP 两个方向都不准）

## 提示词模板

```
【候选】<列出要扫的维度与取值，例如 抽头 65/129/193 × 乘法器上限 不限/96/64/32>
【已有报告】每次跑完落在 <目录>/<版本>/csynth.rpt
【我要的产出】一张 md 表，列固定为：版本 | 拍/采样 | DSP | LUT | FF | BRAM，
             外加一列"相对基线倍数"。表要能被脚本重跑覆盖，不许手改。

【要你做的】
1. 写一个脚本：遍历报告目录 → 抠字段 → 出表。字段用**表头名**定位，不要用列序号。
2. 另外算一次非支配集（拍数与资源两个方向同时更优才算支配），把结果标出来。
3. 明确告诉我：哪些格子的数是估算、哪些是实测，两者**不许混在同一列**。
```

## 三条硬要求（来自踩过的坑）

1. **字段按名字取，不按位置取。** 报告表多了/少了一列就整个错位。
2. **估算与实测分两列。** 同一个版本 csynth 估 6 个 DSP、实测 35 个，混一列就是错数。
3. **表必须由脚本生成。** 手抄的表改一版就失真，而且没人能复核。

## 读表：看非支配前沿，不看单点

把每个版本画在"拍数 vs 资源"平面上，只保留**没有任何其他点能同时打赢**的那几个：

- 落在前沿上的都是"合理选择"，选哪个取决于你要什么；
- 落在前沿里面的点，说明这次的改动方向是负收益 —— 它们的作用是记录"哪条路走不通"；
- **注意坐标轴表达不了"功能"**：少一个功能的版本会在二维上"支配"多功能的版本。
  比较必须在同一功能集内做。

## 模型容易错在哪

- 给一张"全都能跑"的表，不给取舍结论 —— 表本身不是答案
- 拿 csynth 的 Fmax 估算当区分度：实测中同一组候选一律报同一个 Fmax（如 143.58 MHz），
  **完全没有区分度**，真正的时序差别只有布局布线能回答
- 把"改了指令但结果逐项相同"解释成"指令生效但收益小"：更常见的是**指令贴错了层**

## 验收

- 表能一条命令重跑并覆盖自己；
- 每个被引用的数字都能追到某一份具体报告文件；
- 结论写成"我们选了哪一格 + 相邻格为什么放弃"，而不是"我们的数字最好"。

## 来源

本项目 68 版扫描与 `skill/checkers/csynth_report_table.py`；
取舍全貌图见 `report/design_report.md` §4.0 与 `src/python/plot_pareto.py`。

## 修订记录

| 版本 | 日期 | 改动 |
|---|---|---|
| 1.0.0 | 2026-11-02 | 从 68 版扫描与 Pareto 图的做法提炼 |
