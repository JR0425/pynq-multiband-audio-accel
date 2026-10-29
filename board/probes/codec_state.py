"""把 ADAU1761 的寄存器全读一遍，用来拆清楚每条配置调用各写了什么。

背景：出货版（configure + play）能听到声音，我们的 overlay（config_audio_pll +
config_audio_codec + select_mic）放不出声，两边寄存器有 6 个不一样。但两边的
**调用序列**也不同，所以分不清是谁写的。这里把每种组合各跑一遍：

    codec_state.py cfg      出货版位流 + configure()                （不 play、不选麦）
    codec_state.py play     出货版位流 + configure() + play()        （已知能响）
    codec_state.py dmacfg   我们的位流 + pll + codec                 （不选麦）
    codec_state.py dmaen    我们的位流 + pll + codec + 补使能位       （不选麦）
    codec_state.py bad      我们的位流 + pll + codec + select_mic    （放不出声）
    codec_state.py none     什么都不配，只读

`dmaen` 和 `play` 两组对比，如果（除 0x40E8 那个随时在变的）完全一样，
就说明我们的 codec 状态和"能响"那条路已经等同了。

两次运行之间寄存器是残留的，所以脚本**开始时先打印一遍**（`--- before ---`），
跑完再打印（`--- after ---`）。这样即使某条调用"没写任何东西"，也看得出来。

用法（板上，要 root —— i2ctransfer 开 /dev/i2c-1 需要 root）：

    echo xilinx | sudo -S env XILINX_XRT=/usr \
        /usr/local/share/pynq-venv/bin/python3 codec_state.py play
"""

import subprocess
import sys

BUS = "1"
ADDR = "0x3b"
FIR_BIT = "/home/xilinx/fir.bit"
DMA_BIT = "/home/xilinx/dma_exp/fir_dma.bit"
LIB_AUDIO = "/home/xilinx/pynq/lib/libaudio.so"

# 只打印非零的和几个关键项，避免一次刷 128 行看不出重点；
# --all 给全表。
SHOW_ALL = "--all" in sys.argv


def rd(reg):
    """一次完整的寄存器读：START + 写 2 字节地址 + 重复 START + 读 1 字节。
    必须一次走完 —— 拆成两次 write/read 中间会插 STOP，ADAU1761 不认。"""
    p = subprocess.Popen(
        ["i2ctransfer", "-y", BUS,
         "w2@%s" % ADDR,
         "0x%02x" % (reg >> 8), "0x%02x" % (reg & 0xFF),
         "r1"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p.communicate()
    if p.returncode != 0:
        return None
    tok = out.decode().strip().split()
    if not tok:
        return None
    try:
        return int(tok[0], 16)
    except ValueError:
        return None


def dump(title):
    print("--- %s ---" % title)
    miss = 0
    for reg in range(0x4000, 0x4100):
        v = rd(reg)
        if v is None:
            miss += 1
            continue
        if SHOW_ALL or v:
            print("0x%04X 0x%02X" % (reg, v))
    if miss:
        print("### 未应答 %d 个" % miss)


def libaudio():
    import cffi
    ffi = cffi.FFI()
    lib = ffi.dlopen(LIB_AUDIO)
    ffi.cdef("void config_audio_pll(int iic_index);")
    ffi.cdef("void config_audio_codec(int iic_index);")
    ffi.cdef("void select_mic(int iic_index);")
    return lib


def load_fir():
    from pynq import Overlay
    ol = Overlay(FIR_BIT)
    for k, v in ol.ip_dict.items():
        if "audio_codec_ctrl" in v.get("type", "").lower():
            return getattr(ol, k)
    raise SystemExit("ERROR: fir.bit 里没有 audio_codec_ctrl")


def play_tone(a):
    import numpy as np
    n = 48000
    t = np.arange(n * 2) / 2.0 / 48000.0
    y = ((1 << 22) * np.sin(2 * np.pi * 440.0 * t)).astype(np.int32) & 0xFFFFFF
    a.sample_len = n
    a.buffer = y
    a.play()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "none"
    valid = ("none", "cfg", "play", "dmacfg", "dmaen", "bad")
    if mode not in valid:
        print("用法: codec_state.py " + "|".join(valid) + " [--all]")
        return 2

    dump("before (%s)" % mode)

    if mode == "cfg":
        a = load_fir()
        a.configure()
    elif mode == "play":
        a = load_fir()
        a.configure()
        play_tone(a)
    elif mode == "dmacfg":
        from pynq import Overlay
        Overlay(DMA_BIT)
        lib = libaudio()
        lib.config_audio_pll(1)
        lib.config_audio_codec(1)
    elif mode == "dmaen":
        from pynq import Overlay
        Overlay(DMA_BIT)
        lib = libaudio()
        lib.config_audio_pll(1)
        lib.config_audio_codec(1)
        import dma_audio_probe
        dma_audio_probe.codec_unmute()
    elif mode == "bad":
        from pynq import Overlay
        Overlay(DMA_BIT)
        lib = libaudio()
        lib.config_audio_pll(1)
        lib.config_audio_codec(1)
        lib.select_mic(1)

    dump("after (%s)" % mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
