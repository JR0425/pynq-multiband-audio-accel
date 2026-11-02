---
name: hls-bottleneck-triage
description: >
  Decide whether an HLS loop that misses its target II is limited by a real/false
  dependency or by hardware resources, before touching any pragma. Use when csynth
  reports "Unable to enforce a carried dependence constraint", when II is stuck at
  2/4/N, when adding UNROLL changes nothing, or when you are about to add
  ARRAY_PARTITION / ALLOCATION to fix a latency problem.
license: MIT
compatibility: Vitis HLS 2020.2+ / any device. Symptom wording in the log differs slightly across versions.
metadata:
  version: "1.0.0"
  updated: "2026-11-02"
---

# 判断 II 不达标：是依赖问题还是资源问题

## 什么时候用

- csynth 报 `Unable to enforce a carried dependence constraint (II = 1, distance = 1)`
- 目标 II=1，实测卡在 2 / 4 / 8，而且加 pragma 没反应
- 你正准备"加乘法器"或"做数组分割"来解决慢的问题

## 什么时候别用

- 还没跑过 csynth —— 先出报告，凭代码猜是浪费时间
- 问题在**搬数据**而不是算：先看顶层循环的迭代延迟占比（本项目 88% 的拍数花在
  "把延迟线挪一格"上，真正的乘法只占 11 拍）。这种情况加多少 DSP 都没用

## 提示词模板

```
【现象】
csynth 报：<原文粘 HLS 200-880 那条 warning，不要转述>
目标 II=<n>，实测 II=<m>，trip count=<k>，iteration latency=<l>。
顶层循环迭代延迟 <c> 拍/采样，芯片上有 <d> 个 DSP，已用 <u> 个。

【要你做的】
1. 先判断这是「依赖」还是「资源」，并给出你依据的具体日志行；
2. 如果是依赖，说明它是真依赖还是假依赖（跨迭代是否真的重叠），
   并给出改法 + 预期拍数 + 会不会动 DSP 数量；
3. 如果是资源，给出「少加资源会怎样」的预期，以及替代方案；
4. 明确写出：如果我的前提有错，你会在哪一步发现。

【约束】
不要建议先跑一遍再说 —— 你手上的信息已经够判断。不确定的地方直接说不确定。
```

## 模型容易错在哪（实证）

| 错法 | 表现 | 结论 |
|---|---|---|
| **先编机制，后读日志** | 看到 DSP 200/220 就说"乘法器不够"，而日志里 `HLS 200-880 ... distance = 1` 一直写着 | 两类问题在 HLS 里**表现完全一样**（都是 II 达标不了），但验证方式相反：资源不足加资源会变好，假依赖加资源一点不变 |
| 把 `UNROLL` 贴错循环层 | `factor=4` 贴在目标圈上，综合结果与基线**逐项相同**（说明根本没生效）；`factor=2` 反而把外层采样循环展开，DSP 从 200 涨到 392 | 贴错层**不会报错**，只会"没变化"。没变化时先怀疑贴的位置，别怀疑工具 |
| 把改写当成优化 | 把循环体抽成 `static inline` 函数，逻辑一字未改，II 从 4 被推到 14 | 结构重构本身会改变调度，**必须重跑 csynth 对比**，不能假定"等价改写"性能等价 |

## 验收：怎么知道它答对了

1. 它引用的日志行，你去 `csynth.rpt` 里能搜到原文；
2. 它预言的"加资源会不会变好"，跑一版就能证伪 —— 这是最省时间的判据；
3. 改动后 csim 输出**逐字节相同**（若是纯性能改动）。

## 来源

本项目的四段压缩循环 II=4 排查：根因是 `env[]` 数组上 `i` 为变量下标导致的假携带依赖，
与乘法器数量无关；一条 `#pragma HLS DEPENDENCE variable=env inter false` 把 II 从 4 降到 1，
拍数 49 → 36，**DSP 一个没多**。详见 `report/llm_collab_log/2026-10-22_drc_loop_ii.md`。

## 修订记录

| 版本 | 日期 | 改动 |
|---|---|---|
| 1.0.0 | 2026-11-02 | 从 2026-10-22 / 10-23 两次排查提炼 |
