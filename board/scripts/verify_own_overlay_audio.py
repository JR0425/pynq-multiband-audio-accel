"""自建 overlay 第 2 步的验收：**我们自己造的 bit 也能放出声**。

为什么这是单独一步、而不是并进 verify_audio_playback.py：

    之前那次出声用的是 PYNQ 出厂预编译的 base overlay —— 那证明的是「板子会出声」，
    不是「我们的工程会出声」。两者的差别在 PL 里那部分配置是谁生成的。
    这一步换了 bit（`board/overlay/audio.bit`，我们自己跑 Vivado 出的），
    代码一个字没改 —— 于是「出声」这件事就归我们自己的工程了。

    代码不改是设计出来的，不是碰巧：音频 IP 的寄存器地址**故意**和官方对齐
    （0x43C00000），所以 pynq.lib.audio 里写死的偏移、以及设备树里那个
    名为 audio-codec-ctrl 的 UIO 节点，两边通用。

用法（在 PYNQ 板子上跑，必须是在 Jupyter 里，或串口控制台加 sudo）：

    # Jupyter（服务本身是 root 跑的）
    %run verify_own_overlay_audio.py

    # 串口控制台 —— 碰 MMIO 要 root，见 verify_audio_playback.py 的「踩坑」第 1 条
    echo xilinx | sudo -S env XILINX_XRT=/usr \
        /usr/local/share/pynq-venv/bin/python3 verify_own_overlay_audio.py

依赖：同目录下的 verify_audio_playback.py（借它的 make_tone / measure，
不重复写一份生成正弦波的代码）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pynq import Overlay

from verify_audio_playback import (DURATION_S, VOLUME, WAV_PATH, make_tone,
                                   measure)

BITFILE = "audio.bit"
EXPECT_ADDR = 0x43C00000

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("  [%s] %s%s" % ("过" if ok else "没过", name,
                           ("  —— " + detail) if detail else ""))


def main():
    print("=" * 68)
    print("自建 overlay 第 2 步验收：我们自己的 bit 能不能出声")
    print("=" * 68)

    # ---- 1. 加载我们自己的 bit ----
    print("\n1) 加载 %s" % BITFILE)
    try:
        ol = Overlay(BITFILE)
    except Exception as exc:
        print("  加载失败：%s: %s" % (type(exc).__name__, exc))
        print("  检查 .hwh 是否和 .bit 同目录、同文件名")
        return 1

    print("  ip_dict：")
    for name, info in sorted(ol.ip_dict.items()):
        addr = info.get("phys_addr") if isinstance(info, dict) else "?"
        print("    %-24s %s" % (name, addr))

    check("overlay 加载成功", True)

    # ---- 2. IP 认出来了、地址对得上 ----
    print("\n2) 核对音频 IP")
    ip = ol.ip_dict.get("audio_codec_ctrl_0")
    check("ip_dict 里有 audio_codec_ctrl_0", ip is not None)

    if ip is None:
        print("  说明这个 bit 里没有音频 IP —— 是不是加载成第 1 步的 ps_only.bit 了？")
        return 1

    addr = ip.get("phys_addr")
    check("基地址 = 0x%08X" % EXPECT_ADDR, addr == EXPECT_ADDR,
          "实际 0x%08X" % addr if addr is not None else "拿不到地址")
    if addr != EXPECT_ADDR:
        # 地址不对的话 pynq.lib.audio 里写死的偏移就对不上，
        # 后面 configure/play 会读到错的寄存器，没必要继续。
        print("  地址和官方不一致，驱动不能照用，停止。")
        return 1

    # ---- 3. 驱动能不能自动绑上 ----
    # AudioADAU1761.bindto = ['xilinx.com:user:audio_codec_ctrl:1.0']，
    # PYNQ 认 vlnv 自动绑，所以这里取到的应该已经是 AudioADAU1761 实例。
    print("\n3) 驱动绑定")
    audio = ol.audio_codec_ctrl_0
    check("自动绑到 AudioADAU1761",
          type(audio).__name__ == "AudioADAU1761",
          "实际是 %s" % type(audio).__name__)

    # ---- 4. 配 codec（走 PS 侧 I2C1 + 设备树里那个 UIO 节点）----
    print("\n4) 配置 codec")
    try:
        audio.configure()          # 默认 iic_index=1 / uio_name='audio-codec-ctrl'
        audio.set_volume(VOLUME)
        check("configure + set_volume", True, "音量 %d" % VOLUME)
    except Exception as exc:
        check("configure + set_volume", False, "%s: %s" % (type(exc).__name__, exc))
        print("  'Cannot find UIO device' → 设备树里没有 audio-codec-ctrl 节点")
        print("  （地址对了但 UIO 找不到，多半是 .hwh 没被读到）")
        return 1

    # ---- 5. 出声 ----
    print("\n5) 播放 %.0f Hz 正弦波（听！）" % 440.0)
    if not os.path.exists(WAV_PATH):
        print("  生成 %s ..." % WAV_PATH)
        make_tone(WAV_PATH)
    audio.load(WAV_PATH)
    wall, cpu = measure(audio.play)
    print("  播放  墙钟 %.2f 秒   CPU %.2f 秒" % (wall, cpu))
    check("播完了没报错", True)
    print("  ⚠️ 这一条只有你能判：刚才听到声音了吗？")

    print("\n" + "=" * 68)
    failed = [n for n, ok, _ in results if not ok]
    print("自动检查：%d 项，过了 %d 项" % (len(results), len(results) - len(failed)))
    for n in failed:
        print("  没过：%s" % n)
    print("剩下的靠耳朵：有声音 = 第 2 步过。")
    print("=" * 68)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
