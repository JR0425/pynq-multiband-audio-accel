---
name: pl-internal-tone-diagnosis
description: >
  How to prove an FPGA audio datapath actually reaches the headphone jack without
  an oscilloscope or logic analyser: inject a square wave generated inside the
  PL, replacing the processing chain's output, and drive it out through the same
  I2S transmitter, pins and codec. If the tone is audible, then FIFOs, the I2S
  transmitter, pin constraints, the codec's playback mixer and the headphone amp
  are all good, and any remaining silence must come from the source side (mic,
  ADC, front end). Also covers why the tone must bypass any output gain stage,
  why its phase accumulator rides on the sample-valid pulse, and which counters
  to check while it plays.
  Use when the processed audio is silent but all counters look healthy, when
  you cannot tell "the datapath is broken" from "the input is silence", or
  before spending time on electrical-layer theories.
license: MIT
compatibility: any FPGA audio design driving ADAU1761 over I2S; verified on PYNQ-Z2 + Vivado 2020.2
metadata:
  version: "1.0.0"
  updated: "2026-10-28"
---

# 没有示波器，怎么证明音频通路真的通到耳机

2026-10-28 实测。场景：一个全 PL 的音频直连设计，所有计数器都健康
（`und=0 ovf=0 oovf=0`，喂入/吐出都是 48.8 k/s），但耳机里一片安静。
"输入是静音"和"输出链路断了"在计数器上**完全分不开** ——
没有信号的时候，一切看起来都正常。

## 什么时候用

- 处理后的音频没声，但计数器全绿
- 分不清是"没信号进来"还是"信号出不去"
- 手边没有示波器/逻辑分析仪，却要判断引脚上到底有没有波形
- 正要开始怀疑"电气层"（这一步能把大半电气怀疑直接排除）

## 做法：在 PL 里造一个音源，顶替处理链的输出

在输出端的最后一级加一个 mux，用一个控制位选择"片内方波"：

```verilog
ctrl[3] = tone_en

reg [16:0] tone_acc;
reg        tone_sq;
localparam integer TONE_INC = 440;      // 440 Hz
localparam integer FS_NOM   = 48828;    // 100 MHz / 2048

always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        tone_acc <= 17'd0;
        tone_sq  <= 1'b0;
    end else if (out_vld) begin          // 挂在"每样本一次"的脉冲上
        if (tone_acc + TONE_INC >= FS_NOM) begin
            tone_acc <= tone_acc + TONE_INC - FS_NOM;
            tone_sq  <= ~tone_sq;
        end else begin
            tone_acc <= tone_acc + TONE_INC;
        end
    end
end

localparam signed [15:0] TONE_AMP = 16'sd8000;   // 约 -12 dBFS

wire signed [15:0] out_mux = ctrl[3] ? (tone_sq ? TONE_AMP : -TONE_AMP)
                                     : out_gained;
```

要点：

1. **相位累加器挂在 `out_vld` 上**，不挂在自由跑的时钟上。这样频率严格按帧率算，
   与 ADC 那条路无关，且不受前端停顿影响。
2. **测试音不过输出增益级**。如果为了演示调了 +18 dB，方波会被削平，听起来像噪声，
   反而难判断。放在增益之后选。
3. 幅度取 −12 dBFS 左右：足够响，又不至于把 codec 推爆。
4. 只加一个控制位，**不动任何既有通路**，验收完可以永久留着（现场排障很好用）。

## 怎么判读

播放时看这几项，全绿才说明"播放侧"没问题：

| 项 | 期望 | 说明 |
|---|---|---|
| `und`（播放 FIFO 断流） | 不涨 | 发送侧一直有数据 |
| `ovf`（录音 FIFO 溢出） | 不涨 | 收侧没堵 |
| `oovf`（输出 FIFO 溢出） | 不涨 | 下游跟得上 |
| 喂入/吐出速率 | ≈ 48.8 k/s | 每帧一个采样（I2S 每帧两个时隙） |
| 前端相位 | 1→2→3 轮转 | 前端在按 3 拍跑 |

**然后靠耳朵**：听到持续的「嘟」⇒

> PL 内音源 → 输出 FIFO → I2S 发送 → 引脚（BCLK/LRCLK/SDATA）→ codec DAC
> → 播放混音器 → 耳机功放 → 耳机

**这一整条全部成立。** 于是剩下的怀疑只能落在"音源那一侧"：话筒、ADC、
前端、增益。这一刀把问题范围直接砍掉一半，且不需要任何仪器。

反过来，如果测试音也没有，那才轮到电气层（引脚约束、MCLK、焊接）。

## 实测记录

同一颗位流、同一次上电：

```
ctrl=0x0B（run|drc|tone）   耳机里 440「嘟——」        ← 播放链路成立
ctrl=0x03（run|drc）+ 吹气  耳机里有声音               ← 话筒链路也成立
ctrl=0x01（run only）       几乎听不见                 ← 前端单位增益，信号太小
```

三条一起，就把"到底哪一段坏"这件事彻底定死了。

## 配套：顺带把"读数不可信"的坑记下来

- 探针打印的"每秒增量"如果用的是每 50 ms 更新的快照，**数字会小 20 倍**，
  看起来像"喂入远小于 48 k"。要用独立快照算每秒。
- 只判"没丢字"是假绿灯：**没有信号也可以没有丢字**。必须再加一条"喂入速率 ≈ 48.8 k/s"。

## 相关

- `pynq-codec-i2c-access`：codec 读数全是常数时先看 PL 配没配置
- `pynq-audio-playback`：板子不出声的七个坑
