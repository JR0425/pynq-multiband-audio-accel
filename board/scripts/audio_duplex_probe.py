# -*- coding: utf-8 -*-
"""音频通路到底能不能**全双工**？—— 这只探针只读源码 + 测时间，不动算法。

为什么要问这个
--------------
"实时出声"这件事，核那一边已经清了：

    核 0.28 µs/采样（28 拍 @ 100 MHz），48 kHz 的预算 20.83 µs —— **74 倍余量**
    延迟线跨块保持（fir_selftest.py 第 5 节：分块跑和整块跑逐位相同）

所以卡住的不是核，是**音频通路本身**。现在 `fir_audio_loop.py` 的写法是：

    audio.record(3.0)  →  处理  →  audio.play()  →  (下一轮)

录的时候不播、播的时候不录 —— 这是**半双工**。半双工下无论块切多小，
都会出现"录一段、播一段"的交替，中间必然丢音，听起来就是一句一句的。
`fir_audio_loop.py` 的文件头把这件事写成了"要真·实时得把核改成流式接口"，
**那个归因是错的** —— 要改的是**音频通路**，核不用动。

但能不能改成全双工，取决于 `pynq.lib.audio` 这个驱动：
  · `record()` / `play()` 是不是**阻塞**的（等采样数到了才返回）？
  · 两个方向是不是共用同一块 buffer / 同一把锁？
  · IP 里有没有独立的收发 FIFO（有的话，两边就能同时跑）？

这三条**搜不到，只能实测**。所以这只探针做三件事：

  ① 把板上那份驱动的 `record` / `play` / `configure` 源码**原样打出来**
     （`inspect.getsource`）—— 别再靠记忆和二手资料猜 API
  ② 把 audio 对象上所有公开的属性和方法列出来 —— 看有没有"非阻塞启动"的口子
  ③ 录一遍、播一遍、再起两个线程一边录一边播，**量时间**
     判据很硬：如果两个方向的墙钟时间**加起来**才够，
     那它俩就是串行的（半双工）；如果差不多等于**单个**的时间，才是真并行。

用法（板上，要 root，和 fir_audio_loop.py 同一目录）：

    echo xilinx | sudo -S env XILINX_XRT=/usr \
      /usr/local/share/pynq-venv/bin/python3 audio_duplex_probe.py

这个脚本**不改任何寄存器、不放音**（放音前会先把 buffer 填 0，别吓着耳朵）。
"""

import inspect
import os
import sys
import threading
import time

import numpy as np
from pynq import Overlay

BITFILE = "fir.bit"
SEC = 0.20          # 每段多长，短一点，多跑几轮


def hr(title):
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)


hr("0. 加载 overlay")
ol = Overlay(BITFILE)
print("   IP：", sorted(ol.ip_dict.keys()))
if "audio_codec_ctrl_0" not in ol.ip_dict:
    print("ERROR: overlay 里没有 audio_codec_ctrl_0")
    sys.exit(1)

audio = ol.audio_codec_ctrl_0
audio.configure()
print("   audio 对象：%r" % (audio,))
print("   类：%s.%s" % (type(audio).__module__, type(audio).__name__))

# ------------------------------------------------------------------
hr("1. 板上那份驱动的源码（这是唯一可信的 API 说明）")
srcfile = inspect.getsourcefile(type(audio))
print("   文件：%s" % srcfile)
try:
    print("   pynq 版本：%s" % __import__("pynq").__version__)
except Exception as e:                       # noqa: BLE001
    print("   (pynq 版本取不到：%s)" % e)

for meth in ("configure", "record", "play"):
    fn = getattr(type(audio), meth, None)
    if fn is None:
        print("\n   ---- %s()：**没有这个方法** ----" % meth)
        continue
    print("\n   ---- %s() ----" % meth)
    try:
        for line in inspect.getsource(fn).splitlines():
            print("   | " + line)
    except Exception as e:                   # noqa: BLE001
        print("   (取不到源码：%s)" % e)

# ------------------------------------------------------------------
hr("2. audio 对象上有什么")
names = [n for n in dir(audio) if not n.startswith("_")]
print("   属性和方法 %d 个：" % len(names))
for n in names:
    try:
        v = getattr(audio, n)
    except Exception as e:                   # noqa: BLE001
        print("     %-24s <取不到：%s>" % (n, e))
        continue
    if callable(v):
        print("     %-24s method" % n)
    elif isinstance(v, np.ndarray):
        print("     %-24s ndarray shape=%s dtype=%s" % (n, v.shape, v.dtype))
    else:
        print("     %-24s %r" % (n, v))

# 关键一问：有没有"启动但不等"的口子
print("\n   找有没有非阻塞的启动接口：")
hits = [n for n in names if any(k in n.lower() for k in
                                ("start", "stop", "stream", "dma", "async",
                                 "wait", "poll", "ready", "busy", "fifo"))]
print("     命中：%s" % (hits if hits else "**一个都没有**"))

# ------------------------------------------------------------------
hr("3. 量时间：录、播、以及两个线程同时做")


def fill_silence():
    """把 buffer 填成静音，免得下面 play() 的时候放出一声怪响。"""
    buf = getattr(audio, "buffer", None)
    if buf is None:
        return
    try:
        if "sample_len" in names:
            audio.sample_len = SEC * 48000
    except Exception:                        # noqa: BLE001
        pass
    a = np.asarray(buf)
    a[...] = 0


def timeit(fn, n=3):
    best = None
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        dt = time.perf_counter() - t0
        best = dt if best is None else min(best, dt)
    return best


def do_record():
    audio.record(SEC)


fill_silence()
print("   先空跑两轮热身（音频 IP 第一次调用会慢）")
do_record()
try:
    audio.play()
except Exception as e:                       # noqa: BLE001
    print("   (play 热身失败：%s)" % e)

t_rec = timeit(do_record)
t_play = timeit(lambda: audio.play())
print("\n   录 %.2f 秒 → 墙钟 %.4f 秒   （比 %.2f 倍）" % (SEC, t_rec, t_rec / SEC))
print("   播 %.2f 秒 → 墙钟 %.4f 秒   （比 %.2f 倍）" % (SEC, t_play, t_play / SEC))
print("   两个加起来 = %.4f 秒" % (t_rec + t_play))
print("   两者中较大的 = %.4f 秒" % max(t_rec, t_play))

# ---- 两个线程同时做 ----
res = {}


def worker(name, fn):
    try:
        t0 = time.perf_counter()
        fn()
        res[name] = time.perf_counter() - t0
    except Exception as e:                   # noqa: BLE001
        res[name] = e


fill_silence()
t_a = threading.Thread(target=worker, args=("rec", do_record))
t_b = threading.Thread(target=worker, args=("play", lambda: audio.play()))
wall0 = time.perf_counter()
t_a.start()
t_b.start()
t_a.join()
t_b.join()
t_wall = time.perf_counter() - wall0
print("\n   两个线程同起（rec ‖ play）：")
print("     rec  线程内 %.4f 秒   %s" % (res.get("rec"), ""))
print("     play 线程内 %.4f 秒   %s" % (res.get("play"), ""))
print("     一起的墙钟      %.4f 秒" % t_wall)

# ------------------------------------------------------------------
hr("4. 怎么读这些数")
print("""
  判据只有一条：**两个线程同起时的墙钟时间，接近哪一个。**

    墙钟 ≈ max(rec, play)      → 两个方向**真的并行**了 → 全双工有戏，
                                  "实时出声"能在现有 bit 上做出来
    墙钟 ≈ rec + play          → 它们**串行**（共用 buffer 或共用一把锁）→
                                  这个 API 做不了全双工

  还有一种更隐蔽的情况：两个线程都按时返回了，但**其中一个方向没真在收发**
  （驱动里可能有个"忙就返回"的短路）。看出来它靠第 1 节打出来的源码，
  别只看时间。

  如果是串行，出路有三条，代价从小到大：
    (a) 看第 2 节那个"找非阻塞接口"的命中列表 —— 有口子就用口子；
    (b) 自己直接操作 IP 的寄存器做双缓冲（绕开 pynq 的 audio 类，
        需要读 `audio_codec_ctrl` 的寄存器手册 / 自研 IP 的 VHDL）；
    (c) 真的做成 PL 内闭环（I2S 收发之间直接插核）——
        那是排期计划里的"① 全流式"，要重跑 Vivado + 重新验证，
        按排期不该在这里投入。

  不管哪一条，**先有第 1 节的源码**再决定。别凭 API 名字猜。
""")
