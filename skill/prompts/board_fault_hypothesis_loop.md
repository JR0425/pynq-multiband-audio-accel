---
name: board-fault-hypothesis-loop
description: >
  Drive an intermittent board fault to a root cause with one hypothesis, one experiment
  and one falsifier at a time; explicitly forbid changing two variables at once. Use when
  a board hangs, drops data intermittently, or behaves differently on different runs and
  every attempt to explain it "seems to work for a while".
license: MIT
compatibility: Any. The specific kernel knobs (rt throttling, panic_print) are Linux/Zynq, the loop is general.
metadata:
  version: "1.0.0"
  updated: "2026-11-02"
---

# 偶发故障：一次只动一个变量

## 什么时候用

- 板子偶发死机 / 偶发丢数据 / 同一条命令时好时坏
- 你已经"改了几样东西之后它好了"，但说不清是哪一样起了作用
- 有人提出一个听上去很合理的机制，你想验它

## 什么时候别用

- 故障能稳定复现且与时间无关 —— 那是普通 bug，不必上这套流程
- 还没有稳定的"起跑条件"（开机时长、负载、温度）—— 先把它固定下来，否则所有实验都不可比

## 提示词模板

```
【故障】<一句话：什么时候、多久一次、什么现象>
【已排除】<逐条写，含"怎么排除的">
【当前假设】<只写一个>
【起跑条件】<开机 uptime / 负载 / 环境，怎么对齐的>

【要你做的】
1. 给出**一个**实验，能把这个假设证伪（不是证实）；
2. 告诉我"如果假设成立，结果应该是 A；如果不成立，应该是 B"，并且 A 和 B 必须可区分；
3. 列出这个实验可能被哪些因素污染（并说明我怎么控制）；
4. 不要给"多试几次看看"这种建议。
```

## 三条纪律

1. **一次只动一个变量。** 同时动电源、网线、重启三样，然后把功劳记在电源上 —— 这是最常见的自欺。
2. **先定起跑条件。** 起跑 uptime 没对齐时，"改了什么之后不挂了"可能只是"这次跑得短"。
3. **区分"没复现"和"修好了"。** 只测一次通过，等于没有证据。

## 模型容易错在哪

| 错法 | 表现 |
|---|---|
| 把相关性当因果 | "换了电源就不挂了" → 其实换了电源那次正好起跑 uptime 短 |
| 给出无法证伪的机制 | 机制说得通，但推不出任何可观察的差异 —— 这种机制不能用 |
| 建议一次上多个对策 | 全部有效时无法归因，下次复发还得从头查 |
| 把"抓不到栈"当结论 | "这颗板子抓不到栈"是一个**判断**，可以被推翻（本项目的判断就被推翻了：换 `panic_print` 一次打出上千行栈） |

## 验收

- 每个结论都附"若为假，会看到什么"，且这个"什么"后来真的被检查过；
- 最终能写出一句可被第三方复现的因果链，而不是"试了这些就好了"。

## 来源

本项目板子静默硬死的三段排查（`report/llm_collab_log/2026-10-15_board_power_and_rt_priority.md`、
`2026-10-16_board_hang_rcu_stall.md`、`2026-10-17_board_hang_resolved.md`）：
`hung_task`/`softlockup`/`sysrq`/`ftrace` 全被裁掉，只剩 `panic_print=31` 一条路；
六轮实验里自己造的错包括"起跑 uptime 没对齐""在死板子上跑出假 HANG"。

## 修订记录

| 版本 | 日期 | 改动 |
|---|---|---|
| 1.0.0 | 2026-11-02 | 从三段死机排查提炼 |
