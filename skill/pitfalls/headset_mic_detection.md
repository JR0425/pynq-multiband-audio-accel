---
name: pynq-headset-mic-detection
description: >
  Why a microphone stays silent on the PYNQ-Z2 HP+Mic jack even though a headset
  works: U41 (TI TS3A226AE) classifies the plug by the impedance it sees on the
  audio branches, so a line-level load such as an active speaker (~10 kohm) or a
  bare lavalier with nothing else attached falls into the "unrecognized" row and
  both ground FETs close, shorting the microphone pin to ground. Also covers
  CTIA (US, 美标) vs OMTP (China, 国标) pole order, and why a 4-pole-to-3-pole
  adapter inverts a CTIA headset instead of adapting it.
  Use when the speaker works but the mic records only noise floor, when a lavalier
  works on a laptop but not on the board, when a headset works directly but not
  through adapters, or when planning a mic-plus-speaker demo on one TRRS jack.
license: MIT
compatibility: PYNQ-Z2 (TUL) rev with U41 TS3A226AE; PYNQ image 2.7.0
metadata:
  version: "1.0.0"
  updated: "2026-10-26"
---

# 麦克风插着却没声：U41 认不出"高阻负载"

板子的 `HP + Mic` 口里有一颗自动耳机开关 U41（TI **TS3A226AE**）。
它不是"检测到插头就给麦克风放行"，而是**先判断插进来的是什么，再决定地线怎么接**。
判断错了，麦克风触点会被直接短到地。

实测：PYNQ-Z2 + PYNQ 2.7.0，2026-10-26。

## 什么时候用

- 音箱能出声，但录音全是底噪，说话、敲麦都没反应
- 同一个领夹麦插笔记本电脑好好的，插板子就没声
- 四段耳机直插板子能用，但换成"领夹麦 + 转接头"就不行
- 想用一个 `HP + Mic` 口同时接麦克风和音箱

## 什么时候别用

- 耳机和麦都插着、只是偶尔要插拔几次才有声 —— 那是 `pynq_audio_playback.md` 坑 5
- 电平读数贴着满量程 —— 先怀疑读法（坑 7），不是接法
- 走 `Line-in` 口 —— 那个口不经过 U41

## 一句话

**U41 靠音频两条线上的负载阻抗判断插头类型。** 接有源音箱（约 10 kΩ 的线路输入）
或者只插一根悬空的麦，阻抗落在"认不出来"那一档，U41 把两个接地 FET 同时打开，
麦克风触点和地短路，于是"音箱有声、麦没声"。

## 现象

| 接法 | 结果 |
|---|---|
| 四段耳机（带麦）直插板子 | 正常，麦能录 |
| 领夹麦直插板子，别的不接 | 死，int16 域 −72 dB |
| 领夹麦 → 四转三 → 二合一 → 板子 | 死 |
| 美标耳机 → 四转三 → 二合一 → 板子 | 死（比直插还差） |
| 领夹麦（粉口）**＋ 一只老耳机**（绿口）→ 二合一 → 板子 | **通** |

最后一行就是判据：**差别只在绿色那一路有没有一个 32 Ω 的耳机**。
麦克风本身没换、转接头没换、板子没换。

## 原因

TI 数据手册里那张真值表（`TS3A226AE` SCDS346A，Functional Tables: Internal Switches）：

| EN | 认出来的类型 | SW1 | SW2 | FET1 | FET2 |
|---|---|---|---|---|---|
| 0 | — | High Z | High Z | High Z | High Z |
| 1 | 三段耳机 / 音箱（Ring=右, Sleeve=地） | On | On | On | On |
| 1 | 四段 CTIA（Ring2=地, Sleeve=麦） | On | High Z | High Z | **On** |
| 1 | 四段 OMTP（Ring2=麦, Sleeve=地） | High Z | On | **On** | High Z |
| 1 | **认不出来** | On | On | **On** | **On** |

两块接地 FET 分别把 `SLEEVE` 和 `RING2` 拉到地。CTIA 和 OMTP 两行里**只有一个** FET 导通，
所以没被当成地的那一根保住了麦克风信号。**"认不出来"那一行两个都导通 —— 两根都接地，
麦克风信号没有地方可走。**

怎么算"认出来"：同一家族的 TS3A225E 把这段判断结果从 I2C 里读出来了，
是量 `Tip–Sleeve` 和 `Tip–Ring2` 的阻抗，分八档
（`<400 Ω`、`400–800`、…、`≥2800 Ω`）。耳机的喇叭是 32 Ω，落在最低那档；
有源音箱的线路输入是 10 kΩ 量级，落在最高那档。
**226AE 的手册没写这一步的细节**，这段是从 225E 推的，和实测现象对得上，但不是 226AE 的原文。

## 美标和国标

| | TIP | Ring1 | Ring2 | Sleeve |
|---|---|---|---|---|
| CTIA（美标） | 左 | 右 | **地** | **麦** |
| OMTP（国标） | 左 | 右 | **麦** | **地** |

U41 两种都认，前提是它先认出来这是个四段带麦的插头。

**四转三转接头不是"转"接法，是"对调 Ring2 和 Sleeve"。**
它内部就是把这两根换了个位置。所以：

- 给国标（OMTP）麦用：对调之后正好变成板子期待的样子，能用
- 给美标（CTIA）耳机用：本来是对的，一对调反而把麦克风接到地上，**越转越坏**

买之前先确认麦是国标还是美标。今天这只领夹麦商家确认是**国标**。

## 怎么办

### 演示要麦 + 音箱

音箱那一路必须**并一个 32 Ω 的耳机**（或者焊一个 32 Ω 假负载）在输出上，
让 U41 在 `EN` 上升沿那一下能认出"这是耳机类负载"。
二合一绿色母头接一个"一分二"分线器，一头接音箱、一头接耳机（或假负载）。

只接音箱、不并负载，这条路走不通 —— 这不是软件能绕的。

### 只验麦克风

一只带麦的四段耳机直插，是最省事的对照。
如果直插能用、加了转接头就不能用，问题在转接头或麦的制式，不在板子。

## 怎么查（四步，每次只换一样）

1. **先拿一只已知能用的四段耳机直插板子**，确认板子这条路是好的。
   这一步不过，别往下走 —— 先去查坑 5 的插拔锁存。
2. **换成要测的麦**，其他都不动。变了 = 麦或它的线有问题。
3. **加上转接头**，每次只加一个。变了 = 就是刚加的那个。
4. **看绿色那一路有没有负载**。只有麦、没有耳机/音箱时，先并一只耳机再试。

判断"通没通"用电平，不要靠耳朵：int16 域有效值，静音是 7～8，
说话是几百～两千。−72 dB 就是死的。

## 迁移得走的四条

- **"自适应"不等于"任何接法都行"。** 芯片认的是电气特征（阻抗），不是逻辑。
  换负载、换线材都可能把它踢出识别范围。
- **数据手册的真值表比宣传语有用。** 手册写着"两种都支持"，真值表里还有一行
  "认不出来"，那一行才是今天踩的坑。
- **对照实验每次只换一个元素。** 今天这条链上有四段耳机、领夹麦、四转三、二合一、
  音箱五样东西，一起换就分不清是谁的问题。
- **"没声"先分方向。** 出声正常、只有录音没声，说明输出那一半是通的，
  范围立刻缩到输入侧的三四个元件。

## 修订记录

| 版本 | 日期 | 改动 |
|---|---|---|
| 1.0.0 | 2026-10-26 | 初版。依据：2026-10-26 一轮隔离实验（见「现象」表）+ TS3A226AE/TS3A225E 数据手册 |
