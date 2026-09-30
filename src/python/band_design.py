"""频段划分的**唯一定义处**。

段边界、设计方法、系数生成全部收在这个模块里，其它脚本一律 import 它，
不再各自抄一份 —— 这样"边界到底在哪儿"永远只有一个答案。
（改边界就改这里的 BAND_EDGES，然后重新生成系数、重新综合。）

为什么是 500 / 1000 / 2000（倍频程）而不是原来的 300 / 600 / 1000：
    实测同样 257 抽头，倍频程方案最差串音 −32.3 dB，原方案只有 −17.6 dB。
    道理：抽头数固定时过渡带宽度也固定（≈3.3·fs/N），
    边界挤在低频一小段里，几条过渡带互相重叠，自然分不开。
    助听器行业的段边界也基本按倍频程（250/500/1k/2k/4k/8k）——
    跟着听力图的测听频率走，不是跟着电路走。
    完整对照表和推导见 桌面\\PYNQ音频项目\\频段划分方案_调研与建议.md。

设计法为什么是"相减式"：
    不每段各自 firwin 一个带通，而是**只设计低通、相邻相减**。
    各段之和恒等于原信号（重建误差 −309 dB，就是浮点精度），
    而"各自设计"测出来是 +3.2 dB。
"""

from scipy import signal

FS = 48000
NYQ = FS / 2

# 段边界（Hz），从低到高。段数 = len(BAND_EDGES) + 1，低通个数 = len(BAND_EDGES)。
BAND_EDGES = [500, 1000, 2000]

N_BANDS = len(BAND_EDGES) + 1
N_LP = len(BAND_EDGES)

BANDS = (
    [(0, BAND_EDGES[0])]
    + [(BAND_EDGES[i], BAND_EDGES[i + 1]) for i in range(N_LP - 1)]
    + [(BAND_EDGES[-1], NYQ)]
)

# DRC 默认参数 —— Q1.15 下的整数形式就是板上寄存器的复位值。
DRC_THRESHOLD = 0.1
DRC_SLOPE = 0.7


def group_delay(n):
    """线性相位 FIR 的群延迟 = (n-1)/2（n 必须是奇数）。"""
    return (n - 1) // 2


def design_lowpasses(n):
    """3 组低通系数，按边界从低到高。

    firwin 默认就是对称的（Type I 线性相位），所以群延迟精确等于 (n-1)/2 ——
    相减式要求各段**严格等延迟**，靠的就是这一点。
    """
    return [signal.firwin(n, e, fs=FS, pass_zero="lowpass") for e in BAND_EDGES]


def bands_from_lowpasses(lps, n):
    """把 N_LP 组低通还原成 N_BANDS 段的等效系数。

    硬件里是**当场相减**，不存这 4 组；这里只是让 Python 侧能像原来那样
    逐段验证（求和平坦度、串音矩阵）。第 4 段的"全通"就是 δ ——
    即 D 拍延迟的输入，和硬件里用 hist[D] 是同一回事。
    """
    out = []
    for i in range(N_BANDS):
        lower = lps[i - 1] if i > 0 else [0.0] * n
        if i < N_LP:
            upper = lps[i]
        else:
            upper = [0.0] * n
            upper[group_delay(n)] = 1.0
        out.append([u - l for u, l in zip(upper, lower)])
    return out
