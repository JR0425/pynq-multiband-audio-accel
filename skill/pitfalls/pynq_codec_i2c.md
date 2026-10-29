---
name: pynq-codec-i2c-access
description: >
  Why reading the ADAU1761 audio codec over I2C on a PYNQ-Z2 can return the same
  constant (0x00 or 0x01) for every register even though the bus answers at
  0x3b and the write command exits 0: the codec's I2C address pins are driven
  from the FPGA fabric (an xlconstant of 3), so with no bitstream loaded they
  float and the codec does not really answer, while a codec left in a wedged
  state answers but ignores writes. Also covers the exact unmute values
  (0x401C=0x21, 0x401E=0x41, 0x4023/0x4024=0xF3) and the reference register
  values that prove the codec is alive, plus the "power-cycle to recover" rule.
  Use when codec registers all read back identical, when writes report success
  but the value does not change, when the headphone volume register reads 0x01
  instead of 0xE5/0xF3, or before blaming your own RTL for silence.
license: MIT
compatibility: PYNQ-Z2 + ADAU1761; PYNQ image 2.7.0; design instantiates an xlconstant driving codec_addr
metadata:
  version: "1.0.0"
  updated: "2026-10-28"
---

# codec 寄存器全是同一个数：先看 PL 配没配置

2026-10-28 实测。症状极有迷惑性：

```
0x4000 = 0x00   0x4014 = 0x00   0x4019 = 0x00   0x4023 = 0x00 ...
（换个状态之后）
0x4000 = 0x01   0x4014 = 0x01   0x4019 = 0x01   0x4023 = 0x01 ...
```

而 `i2cdetect -y -r 1` **确实在 `0x3b` 看到设备**，`i2ctransfer` **退出码 0**，
写入也报成功。于是很容易得出"寄存器被配错了 / codec 坏了 / 我们的 RTL 有问题"
这三个都不对的结论。

## 根因

**这颗板子的音频 codec 的 I2C 地址脚（ADDR0/ADDR1）是由 FPGA 驱动的。**
设计里是一个常量为 3 的 `xlconstant` 接到 `codec_addr` 端口（3 → I2C 地址 `0x3b`）。

- **PL 没配置** ⇒ 这两根脚悬空 ⇒ codec 不应答 ⇒ 读回全 `0x00`，
  但 `i2ctransfer` 仍然返回 0 和退出码 0（不报错）；
- **PL 配置过、但 codec 被历次实验写拧了** ⇒ 读回全 `0x01`，写入报 OK 但不生效。

两种情况都长得像"配置错"，其实**一个数是常数就说明根本没读到寄存器**。

## 什么时候用

- 所有寄存器读回同一个常数
- 写入返回成功、回读不变
- 耳机音量寄存器 `0x4023` 读到 `0x01` / `0x00` 而不是 `0xE5`（出厂）或 `0xF3`（未静音）
- 正准备因为"没声音"去改 RTL

## 正确顺序（顺序本身就是解法）

```
1. Overlay(你的 bit)          # 必须先配置 PL，否则地址脚悬空
2. 读一遍                      # 应看到出厂值
3. codec_init()               # libaudio 配置 + codec_unmute()
4. 再读一遍                    # 0x4023 应变 0xF3
5. 确认 (0x4023 & 0x02) != 0   # bit1=1 才是未静音
6. 才放音
```

脚本见 `board/probes/pl_demo_check.py`（就是照这个顺序写的）。

## 判据：两组"codec 活着"的基准数

出货 overlay（`fir.bit`）刚加载完：

```
0x4000=0F  0x4014=20  0x4019=33  0x401C=00  0x401E=00  0x4023=E5  0x4024=E5  0x4029=03
```

`codec_init()` 之后：

```
0x401C=21  0x401E=41  0x4023=F3  0x4024=F3
```

**注意 `codec_unmute` 的正确值不是 `0x01/0x01/0xE7/0xE7`。**
那是早期写错的版本：`0x401C` 停在 `0x01` 时耳机照样不响 —— 起作用的是
`0x401C` 的 bit5（左）和 `0x401E` 的 bit6（右），所以是 `0x21` / `0x41`。
`0x4023/0x4024` 是耳机音量码 0xF3（约 +0.5 dB，bit1=1 解除静音）。

读法必须**一次走完**（START + 写 2 字节地址 + 重复 START + 读 1 字节）：

```python
subprocess.Popen(["i2ctransfer", "-y", "1", "w2@0x3b",
                  "0x%02x" % (reg >> 8), "0x%02x" % (reg & 0xFF), "r1"], ...)
```

拆成两次 write/read 中间会插 STOP，ADAU1761 不认。

## codec 被写拧了怎么办：断电重上电

现象：所有寄存器读回同一常数，写入无效，`codec_init()` 也救不回来。
处理：**拔电源 5 秒再插回去**，然后按上面的顺序重来。
（PL 的 `Overlay()` 重载**不会**复位 codec，所以重载 overlay 解决不了。）

## 顺带一条：PL 内部测试音能省掉整轮怀疑

codec 确认活着、但耳机还是没声时，**不要先去查电气层**。
在 PL 里加一个内部测试音（见 `pl-internal-tone-diagnosis`），
能直接证明"PL → I2S → 引脚 → codec → 耳机"这一段通不通，
把"音源的问题"和"播放链路的问题"劈开。

## 相关

- `pl-internal-tone-diagnosis`：用一个片内方波把数据通路和音源分开
- `pynq-audio-playback`：七个坑，坑 5 是耳机开关锁存
- `vivado-windows-batch-build`："文件不存在"的假象
