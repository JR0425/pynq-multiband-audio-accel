# 核在整机上跑一遍的验收脚本（板载运行）
#
# 这一步查的不是算法（算法在电脑上用 csim 验过了），查的是**接线对不对**：
# 寄存器写得进吗、地址对不对、核能不能真的摸到 DDR、算完能不能收回来。
#
# 判据只有一条硬的 —— **直通检验**：
#   把压缩关掉（bypass），输入是什么，输出就必须是"输入延后 96 拍"，**一个比特都不差**。
#   这条能同时证明：寄存器写对了、两个缓冲区地址传对了、DDR 读写通了、块长对。
#   只要有哪根接线接错，这一条必挂，而且挂得很明显。
#
# 用法（板子上，要 root）：
#   echo xilinx | sudo -S env XILINX_XRT=/usr \
#     /usr/local/share/pynq-venv/bin/python3 fir_selftest.py

import sys
import numpy as np

from pynq import Overlay
from fir_core import FirMultiband

BITFILE = "fir.bit"
N = 1000                      # 一块的采样数
FS = 48000.0

ok_all = True


def check(name, passed, detail=""):
    global ok_all
    ok_all = ok_all and passed
    print("  [%s] %s%s" % ("OK " if passed else "FAIL", name,
                           ("  " + detail) if detail else ""))
    return passed


print("=== 1. 加载 overlay ===")
ol = Overlay(BITFILE)
print("  里面的 IP：", sorted(ol.ip_dict.keys()))

if "fir_multiband_0" not in ol.ip_dict:
    print("ERROR: overlay 里没有 fir_multiband_0 —— bit 是不是旧的？")
    sys.exit(1)

ip = ol.ip_dict["fir_multiband_0"]
print("  核的地址：", hex(ip["phys_addr"]), " 范围：", hex(ip["addr_range"]))
check("核在 0x43C10000", ip["phys_addr"] == 0x43C10000, hex(ip["phys_addr"]))

k = FirMultiband(ol.fir_multiband_0.mmio)

# ---------------- 造一块输入 ----------------
# 用一段双音 + 一点噪声，比纯正弦更能暴露接线错误
t = np.arange(N, dtype=np.float64) / FS
sig = (0.5 * np.sin(2 * np.pi * 300 * t)
       + 0.3 * np.sin(2 * np.pi * 3000 * t))
rng = np.random.default_rng(1)
sig = sig + 0.02 * rng.standard_normal(N)
x = np.clip(np.round(sig * 32767.0), -32768, 32767).astype(np.int16)
print("\n=== 2. 输入 ===  长度 %d，峰值 %d" % (N, int(np.abs(x).max())))

# ---------------- 直通检验 ----------------
print("\n=== 3. 直通检验（bypass = 1，不看压缩）===")
y, dt, blocks = k.process(x, reset=True, bypass=True)

D = FirMultiband.GROUP_DELAY
ref = np.zeros_like(x)
ref[D:] = x[:N - D]          # 延迟线从 0 起，所以前 96 个是 0
mism = int(np.count_nonzero(y != ref))
print("  延迟 = %d 拍，跑完耗时 %.4f 秒" % (D, dt))
check("输出逐位等于「输入延后 96 拍」", mism == 0,
      "错配 %d / %d 个" % (mism, N))

# ---------------- 开启压缩 ----------------
print("\n=== 4. 开启压缩（阈值 0.1，压缩比 0.7）===")
y2, dt2, _ = k.process(x, reset=True, bypass=False)

diff = int(np.count_nonzero(y2 != y))
print("  与直通输出不同的采样：%d / %d" % (diff, N))
check("压缩真的起作用了（输出和直通不一样）", diff > 0)

rms_in = float(np.sqrt(np.mean(x[D:].astype(np.float64) ** 2)))
rms_by = float(np.sqrt(np.mean(y[D:].astype(np.float64) ** 2)))
rms_cp = float(np.sqrt(np.mean(y2[D:].astype(np.float64) ** 2)))
print("  有效值：输入 %.0f ｜ 直通 %.0f ｜ 压缩后 %.0f" % (rms_in, rms_by, rms_cp))
check("压缩后整体变轻（动态范围压缩该有的样子）", rms_cp < rms_by,
      "%.1f -> %.1f" % (rms_by, rms_cp))

# ---------------- 每段增益（2026-10-09 新增的 0x50 寄存器）----------------
# 这三条全是**精确判据**，不是"看着差不多"。理由：增益这条链路上唯一的
# 硬证据就是"给一个极端的数，输出必须是唯一确定的那个值"。
#   · 增益给 0    -> 输出必须**整块全 0**。乘法那一步要是没接上（寄存器读到 0
#                    也算、线接错了也算），这一条必挂。
#   · 增益给 -1.0 -> 输出必须**逐位等于 +1.0 那次取反**。drc() 对负数是奇函数
#                    （代码里 symbol 单独处理），所以取反必须精确成立 ——
#                    只有输出饱和到 ±32767/±32768 的那几个点会差 1 个 LSB。
#   · 直通模式下把增益给到量程顶，输出必须**一个比特都不变**。
#                    bypass 那条路故意不吃增益（见 fir_multiband.cpp 里那条注释），
#                    它是"结构接错了 / 量化误差大了"唯一的分辨手段，不能被增益污染。
print("\n=== 5. 每段增益（寄存器 0x50）===")

y_g8, _, _ = k.process(x, reset=True, bypass=False, gain=(256,) * 4)

y_g0, _, _ = k.process(x, reset=True, bypass=False, gain=(0,) * 4)
nz = int(np.count_nonzero(y_g0))
check("增益全给 0 -> 输出整块全 0", nz == 0, "非零采样 %d / %d" % (nz, N))

y_gn, _, _ = k.process(x, reset=True, bypass=False, gain=(-256,) * 4)
d = np.abs(y_gn.astype(np.int32) + y_g8.astype(np.int32))
# ⚠️ 容差是 2 个 LSB，不是 0。这不是放水，来源是**量化的不对称**：
#    drc_t(Q2.22) 用 AP_TRN，即**朝 −∞ 截断**，而"朝 −∞ 截断"不是奇函数
#    —— 正的往下抹、负的往更负抹，band 和 −band收进 drc_t 之后就差了 2^-22。
#    这点偏差经过压缩比传到输出上最多 2 个 LSB（≈ −84 dBFS）。
#    上板实测的分布：1000 个点里 991 个差 1、8 个差 2、1 个差 0。
#    （差 2 的那 8 个，是差值刚好压在截断边界上被抬进下一条格；差 0 的那个
#      四段都在阈值以下、drc 直接返回原值，没有量化。）
#    这条判据依然很硬：寄存器要是没接上、或乘法被优化掉，y_gn 会等于 y_g8，
#    差值直接是 3 万量级 —— 不是 2。
check("增益 -1.0 -> 等于 +1.0 那次取反（差 ≤ 2 个 LSB）",
      int(d.max()) <= 2, "最大差 %d，均值 %.2f" % (int(d.max()), float(d.mean())))

y_bt, _, _ = k.process(x, reset=True, bypass=True, gain=(32767,) * 4)
mbt = int(np.count_nonzero(y_bt != y))
check("直通模式下增益不起作用（bypass 不吃增益）", mbt == 0,
      "错配 %d / %d" % (mbt, N))

# ---------------- 分块连续性 ----------------
# 分块时只在第一块 reset，块边界不该炸出咔声 —— 判据是分块结果和整块结果一致
print("\n=== 6. 分块连续性（每块 157 个点，切成 7 块）===")
y3, dt3, bl = k.process(x, reset=True, bypass=True, chunk=157)
mism3 = int(np.count_nonzero(y3 != y))
check("分块跑和整块跑结果相同（延迟线跨块保持住了）", mism3 == 0,
      "错配 %d 个，切了 %d 块" % (mism3, len(bl)))

# ---------------- 速度 ----------------
print("\n=== 7. 速度 ===")
n_real = 48000.0
budget = N / n_real
print("  %d 个采样，核跑了 %.4f 秒（实时预算是 %.4f 秒）" % (N, dt2, budget))
print("  实时余量：%.1f 倍" % (budget / dt2 if dt2 > 0 else float("inf")))
print("  注：这个时间含 Python 的寄存器读写和 cache 维护开销，不是纯硬件速度")

print("\n==== 结论：%s ====" % ("全过" if ok_all else "有失败项"))
sys.exit(0 if ok_all else 1)
