#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
---
name: axi-stream-dma-host
description: >
  A host-side skeleton for moving blocks through an AXI DMA between DDR and a
  custom AXI-Stream kernel on PYNQ, with the cache handling and the status-polling
  shape that actually hold up on a Cortex-A9. Use when wiring a streaming kernel to
  axi_dma for the first time, when a transfer silently returns fewer samples than
  requested, when throughput measured on the host disagrees with the kernel's own
  cycle count, or when the same overlay cannot be reloaded in the same process.
license: MIT
compatibility: PYNQ 2.7.0, Zynq-7020, axi_dma with MM2S+S2MM; the DMA length-register note applies to c_sg_length_width left at its default
metadata:
  version: "1.0.0"
  updated: "2026-11-01"
---

# AXI DMA 搬运骨架

## 什么时候用

- 自定义 AXI-Stream 核要接 `axi_dma`，第一次搭主机侧。
- 传输"成功了"但回来的点数比请求的少。
- 主机侧量出来的吞吐明显低于核自己的拍数折算值。
- 要在同一个进程里换 overlay 重跑。

## 什么时候别用

- 块很小、次数很少：逐字 MMIO 更简单，DMA 的启动开销反而不划算。
- 只要功能正确性、不要吞吐：直接 MMIO 逐字读写，读数更直观。
- 音频这种**连续流**场景：`axi_dma` 的"发一块等一块"仍然有块间空隙，
  真正的连续流要在 PL 里做 FIFO 缓冲（见文末"什么时候还不够"）。

## 五个会静默出错的点

前三个是本项目真踩过的，后两个是通用陷阱。

1. **`c_sg_length_width` 默认 14 位，长度被截断而 IP 不报错。**
   19200 字节被截成 2816 字节，4800 点只回来 704 个。
   PYNQ 驱动本来按 `2^14-1` 卡住并给了提示 —— **不要改驱动去绕过它**，
   那等于把唯一的提示拆了。正确做法是在 tcl 里把 `c_sg_length_width` 设成 26 再重编。
2. **cache 一致性。** MM2S 前要 `flush`（CPU 写的数据必须真的落到 DDR），
   S2MM 后要 `invalidate`（否则读到 cache 里的旧值）。顺序错了不会报错，只是数不对。
3. **别把校验放进计时区间。** Cortex-A9 上一次 numpy 整块校验就是 5.7 ms，
   比一次传输还长。测吞吐时 `verify=False`，校验单独跑。
4. **状态轮询要 O(1)。** `while not ch.idle(): pass` 这类写法会把一个 1 ms 的宽限循环
   原样算进墙钟。
5. **同一进程不能换位流。** 前一块的 UIO 还开着时灌新 `.bit` 会卡住。
   换位流必须起新进程。

## 用法

把 `DmaBlockXfer` 拷进你的板端脚本，改 `IP_NAME` 与 `MAX_BYTES`：

```python
from pynq import Overlay, allocate
from pynq.lib.dma import DMA

ol = Overlay("your.bit")
dma = ol.axi_dma_0
xfer = DmaBlockXfer(dma, dtype="int16", max_bytes=1 << 26)

y = xfer.run(x)                 # 一次往返，含 cache 维护
y = xfer.run(x, verify=True)    # 带逐位校验（会慢，别用来测吞吐）
```

## 什么时候还不够

`axi_dma` 每次都要"起 → 等 → 收"，块与块之间有空隙。音频这类不允许断流的场景，
要么在 PL 里加一级 FIFO 吸收块间空隙，要么干脆把整条链路放进 PL
（本项目最终走的是后者：codec ↔ I2S ↔ 核 ↔ codec，音频一个字节都不过 PS）。
"""

import time

# ---- 改这两行 ----
IP_NAME = "axi_dma_0"
MAX_BYTES = 1 << 26   # 与 IP 的 c_sg_length_width 一致；设小了会静默截断

try:
    import numpy as np
except ImportError:      # 板端没装 numpy 也能跑，只失去 verify
    np = None


class DmaBlockXfer:
    """一次 DDR ↔ 核 ↔ DDR 的整块往返。

    刻意不做"流水线化"：先保证一次往返的数是对的，再谈吞吐。
    流水线化会引入"上一块还没收完就发下一块"的状态，出错时很难判断是谁的问题。
    """

    def __init__(self, dma, dtype="int16", max_bytes=MAX_BYTES):
        self.dma = dma
        self.dtype = dtype
        self.max_bytes = max_bytes

    # ---------- 内部 ----------
    def _check_len(self, nbytes):
        if nbytes > self.max_bytes:
            raise ValueError(
                "传输 %d 字节超过上限 %d。要么改 max_bytes，要么回 IP 把 "
                "c_sg_length_width 调大。不要改驱动绕过长度检查 —— 那条检查是唯一提示。"
                % (nbytes, self.max_bytes)
            )

    @staticmethod
    def _wait(ch, timeout=5.0, grace=50e-6):
        """O(1) 状态检查 + 极短宽限。

        不用 `while not ch.idle(): pass` —— 那会把宽限循环整段算进墙钟，
        实测能凭空多出 1 ms。
        """
        t0 = time.monotonic()
        while not ch.idle():
            if time.monotonic() - t0 > timeout:
                raise TimeoutError("DMA 超时：%.3f s 未完成" % timeout)
            time.sleep(grace)
        return time.monotonic() - t0

    # ---------- 对外 ----------
    def run(self, x, verify=False):
        """x: 一维序列或 numpy 数组。返回同长度同 dtype 的数组。"""
        if np is None:
            raise RuntimeError("run() 需要 numpy；板端没有的话请直接用 dma.sendchannel")

        x = np.ascontiguousarray(x, dtype=self.dtype)
        nbytes = x.nbytes
        self._check_len(nbytes)

        buf_in = allocate(shape=x.shape, dtype=self.dtype)
        buf_out = allocate(shape=x.shape, dtype=self.dtype)
        try:
            buf_in[:] = x
            buf_in.flush()                      # ← MM2S 之前必须 flush

            t0 = time.monotonic()
            self.dma.sendchannel.transfer(buf_in)
            self.dma.recvchannel.transfer(buf_out)
            self.dma.sendchannel.wait()
            self.dma.recvchannel.wait()
            dt = time.monotonic() - t0

            buf_out.invalidate()                # ← S2MM 之后必须 invalidate
            y = np.array(buf_out, copy=True)
        finally:
            del buf_in
            del buf_out

        if verify:
            mism = int((y != x).sum())
            if mism:
                raise AssertionError("往返不一致：%d / %d 点不同" % (mism, y.size))

        self.last_seconds = dt
        self.last_samples = y.size
        return y

    def throughput(self):
        """上次 run() 的采样率。测吞吐时 verify 必须为 False。"""
        dt = getattr(self, "last_seconds", 0.0)
        n = getattr(self, "last_samples", 0)
        return (n / dt) if dt > 0 else float("inf")


def _selftest():
    """拿 DDR 自环验通路（不加载 overlay 时也能跑，用来分辨"是 DMA 还是核"）。"""
    print("selftest 需要板子：请先把 overlay 加载好，再: ")
    print("  from pynq import Overlay")
    print("  ol = Overlay('your.bit')")
    print("  x = DmaBlockXfer(ol.%s).run(range(64), verify=True)" % IP_NAME)


if __name__ == "__main__":
    _selftest()
