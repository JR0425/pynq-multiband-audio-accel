---
name: measure-tool-before-subject
description: >
  Triage a measurement that "looks like a hardware fault" by vetting the measurement
  path first: sign extension, endianness, scaling, unit, probe overhead, capture window.
  Use when a level reads full-scale, a signal looks self-oscillating, throughput drops
  only when your instrument is attached, or two readings of the same quantity disagree.
license: MIT
compatibility: Any. Written from PYNQ/Zynq cases but the order of suspicion is general.
metadata:
  version: "1.0.0"
  updated: "2026-11-02"
---

# 先怀疑测量工具，再怀疑被测对象

## 什么时候用

- 读数贴满量程（"自激了"）、波形像台阶、频谱出现不可能的能量
- 只在**开着探针**时出现丢数据 / 丢块 / 变慢
- 同一个量，两种测法给出两个数，而两边代码看起来都没问题

## 什么时候别用

- 已经做过"换成已知正确的信号源"这一步，且结果仍然异常
- 故障在**关掉所有探针**时也稳定复现 —— 那就真的是被测对象

## 提示词模板

```
【现象】<一句话，不要带因果解释>
【我的读数】<数值 + 单位 + 你是怎么读出来的（哪几个字节、怎么解释）>
【我认为的原因】<写出来，但标注"这是我的假设">
【不要你做的事】不要顺着我的假设往下推。

【要你做的】
1. 列出这个读数可能出错的全部环节（读取位宽/符号扩展/字节序/量纲/饱和/时间窗口/探针开销）；
2. 对每个环节给出**一次能定生死的实验**（改哪一处、看什么变化），按成本从小到大排；
3. 指出我的假设如果成立，哪些现象**应该**同时出现 —— 如果这些现象没有，假设就错了。
```

## 模型容易错在哪（实证）

| 错法 | 后果 |
|---|---|
| 顺着用户的假设向下推 | 一次"追不存在的故障"查了三个晚上，根因是**读法**：24 位补码没做符号扩展，数字零被读成 rms 46341（= 65535/√2） |
| 把"数变了"当成"硬件变了" | 丢块是探针自己造成的：开着逐块跟踪每块多花 15~25 µs，关掉后 600 秒 0 丢块 |
| 忽略单位与归一化 | 加速比虚高 18%，因为两边跑的不是同一套系数、同一个采样率 —— 这类错不报错，只让数字好看 |

**一条可直接照抄的判据：** 如果"故障"只在某个读法/某个探针下出现，
先假定是读法问题，直到有一个**不依赖该读法**的对照把它否掉。

## 验收

1. 每个候选环节都有一次实验，且实验是**单变量**的（同时动两样等于没动）；
2. 最后能用一句话说清"数字是怎么读出来的"，并指出哪一步曾经读错。

## 来源

本项目三处：麦克风电平"满幅自激"实为符号扩展缺失（`report/llm_collab_log/2026-10-10_mic_input_full_scale.md`）；
丢块实为探针开销（`2026-10-24_dropout_was_the_probe.md`）；
峰值测在错误的点上（`2026-10-29_peak_measured_at_wrong_point.md`）。

## 修订记录

| 版本 | 日期 | 改动 |
|---|---|---|
| 1.0.0 | 2026-11-02 | 三处同类事故合并 |
