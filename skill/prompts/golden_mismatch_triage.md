---
name: golden-mismatch-triage
description: >
  Structured interrogation when hardware output does not match a golden reference:
  best-shift search, bit-exact compare, pass-through identity, per-stage comparison.
  Use when SNR is only a few dB, correlation is low, output "looks the same" but differs,
  or when you cannot tell a structural bug from a wrong delay.
license: MIT
compatibility: Any. The pass-through arithmetic identity requires a subtractive filter bank; other checks are generic.
metadata:
  version: "1.0.0"
  updated: "2026-11-02"
---

# 逐位对不上时的三问

## 什么时候用

- 硬件/HLS 输出与参考"看着一样"但逐位不同
- 相关系数只有零点几，SNR 只有几 dB
- 不确定错在**结构**还是错在**延迟/对齐**

## 什么时候别用

- 两边精度本来就不同（float64 vs Q1.15）—— 这时只能报 SNR 并**写明精度不同**，逐位比对没有意义
- 还没确定两边跑的是同一份输入、同一套系数、同一采样率 —— 先做这个

## 提示词模板

```
【两边是什么】A=<实现与精度>，B=<实现与精度>
【输入】<是否同一份文件、同一采样率、同一系数来源>
【现象】逐位不同的点数 <n>/<N>，最大绝对误差 <e> LSB，最佳位移 <d> 采样时 SNR <s> dB

【要你做的，按这个顺序回答，不要跳】
1. 如果两边**只有延迟不同**，最佳位移处的残差应该长什么样？现在是这样吗？
2. 给出一个能区分「结构错」和「对齐错」的单次实验（不要给一堆）。
3. 如果结构错，按"最可能先错在哪一级"排序，并说明每一级的判据。
4. 明确写出：在拿到什么证据之前，你不该下结论。
```

## 三问的具体判据

| 问 | 实验 | 若成立 |
|---|---|---|
| ① 是不是只差延迟？ | 搜最佳位移后重比 | 位移处逐位相同 → **只是对齐**，去查群延迟常量 |
| ② 结构对不对？ | **直通检验**：把滤波器做成直通，输出应等于延迟 D 拍的输入 | 不成立 → 结构/系数/位宽错，与压缩逻辑无关 |
| ③ 哪一级先错？ | 逐级引出中间量（累加器、移位后、饱和后） | 累加器就错 → 乘加树；累加器对、输出错 → 移位量或饱和 |

**为什么先比累加器：** 输出是"右移 + 饱和"之后的结果，会把低若干位全扔掉。
只比输出的话，"乘法算错一点"和"移位量写错"长得一模一样。

## 模型容易错在哪

- 直接给"可能是系数错了/可能是位宽不够"的清单，不给判定顺序 —— 清单不能定位，**顺序**才能定位
- 把"逐位相同"当成功：如果输入本身是静音或压缩器从未启动，逐位相同只证明直通没被弄坏。
  **必须让被测功能真的被激励**（本项目的教训：一份 `quiet` 音源包络最高值只有阈值的一半，
  压缩器一次都没启动，测试仍然"全过"）

## 验收

三问都有答案，且每个答案都对应一次**单变量**实验；最后能用一句话说出"错在哪一级"。

## 来源

本项目 `skill/checkers/golden_compare.py` 把这三问做成了带退出码的脚本；
判据的形成过程见 `report/llm_collab_log/2026-10-02_figures_and_one_lsb.md` 与
`report/design_report.md` §5 的方法论三条。

## 修订记录

| 版本 | 日期 | 改动 |
|---|---|---|
| 1.0.0 | 2026-11-02 | 把报告 §5 的方法论抽成可复用提问顺序 |
