"""把仿真波形画成报告用的图，输出到 data/figures/report/。

报告里图 1、图 3 用的是 Vivado 波形窗口的截图（sim/rtl_sim/wave_*.wcfg 定的视角），
这里画的是同一批数据的备用版本；图 2 的传输曲线只能用脚本画。

数据不是手画的，全部来自 xsim 自己吐的文件：

  fig5_1_fe_pipeline.png   sim/rtl_sim/xsim_work_wave_fe/wave.vcd
                             tb_fir_mb_fe 开了 -testplusarg WAVE 的 VCD
  fig5_2_limiter.png       sim/rtl_sim/xsim_work_wave_lim/lim_wave_inout.txt
                           sim/rtl_sim/xsim_work_wave_lim/lim_curve.txt
                             tb_lim_wave 的输入/输出逐点记录

先跑 bash sim/rtl_sim/run_xsim_wave.sh，再跑本脚本。
"""

import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plot_report_figures import ACCENT, BLUE, GREEN, GREY, INK, OUT, save  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SIM = os.path.join(ROOT, "sim", "rtl_sim")

FS = 32768.0


# ---------------------------------------------------------------- VCD
def parse_vcd(path):
    """只认 xsim 2020.2 写出来的那点语法：$var / #time / 标量 / b 向量。"""
    names, data, scope = {}, {}, []
    t = 0
    skip = False
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            ln = raw.strip()
            if not ln:
                continue
            if skip:
                if ln == "$end":
                    skip = False
                continue
            if ln.startswith("$date") or ln.startswith("$version") \
                    or ln.startswith("$timescale") or ln.startswith("$comment"):
                skip = True
            elif ln.startswith("$var"):
                p = ln.split()
                width, vid, name = int(p[2]), p[3], p[4]
                names[vid] = (".".join(scope + [name]), width)
                data.setdefault(vid, [(0, "x")])
            elif ln.startswith("$scope"):
                scope.append(ln.split()[2])
            elif ln.startswith("$upscope"):
                scope.pop()
            elif ln.startswith("#"):
                t = int(ln[1:])
            elif ln.startswith("$"):
                continue
            elif ln[0] in "bB":
                val, vid = ln[1:].split()
                data[vid].append((t, val.lower()))
            elif ln[0] in "01xzXZ":
                data[ln[1:]].append((t, ln[0].lower()))
    return names, data


def segs(change, t_end):
    """[(t, v)] -> [(t0, t1, v)]"""
    out = []
    for i, (t, v) in enumerate(change):
        t1 = change[i + 1][0] if i + 1 < len(change) else t_end
        if t1 > t:
            out.append((t, t1, v))
    return out


# ---------------------------------------------------------------- 画数字波形
def draw_panel(ax, rows, t0, t1):
    """rows: [(label, width, segs, fmt)]；标量 v 是 "0"/"1"/"x"，总线 v 是二进制串。"""
    n = len(rows)
    hi, lo = 0.72, 0.28
    for i, (label, width, sg, fmt) in enumerate(rows):
        yb = n - 1 - i
        yhi, ylo = yb + hi, yb + lo
        ax.text(t0 - (t1 - t0) * 0.012, (yhi + ylo) / 2, label,
                ha="right", va="center", fontsize=9, color=INK)
        prev = None
        for (ta, tb, v) in sg:
            if tb <= t0:
                prev = v
                continue
            if ta >= t1:
                break
            a, b = max(ta, t0), min(tb, t1)
            if width == 1:
                if v == "1":
                    ax.plot([a, b], [yhi, yhi], color=INK, lw=1.4)
                elif v == "0":
                    ax.plot([a, b], [ylo, ylo], color=INK, lw=1.4)
                else:
                    ax.plot([a, b], [(yhi + ylo) / 2] * 2, color=GREY,
                            lw=1.4, ls=(0, (2, 1)))
                if prev is not None and prev != v and ta > t0:
                    ya = yhi if prev == "1" else (ylo if prev == "0" else (yhi + ylo) / 2)
                    yb2 = yhi if v == "1" else (ylo if v == "0" else (yhi + ylo) / 2)
                    if ya != yb2:
                        ax.plot([a, a], [ya, yb2], color=INK, lw=1.4)
            else:
                ax.add_patch(Rectangle((a, ylo), b - a, yhi - ylo,
                                       fc="#eef2f5", ec=INK, lw=1.0))
                iv = None if v in ("x", "z") else int(v, 2)
                if iv is None:
                    ax.text((a + b) / 2, (yhi + ylo) / 2, "x", ha="center",
                            va="center", fontsize=8, color=GREY)
                elif (b - a) > (t1 - t0) * 0.05:
                    ax.text((a + b) / 2, (yhi + ylo) / 2, fmt(iv), ha="center",
                            va="center", fontsize=7.5, color=INK,
                            family="DejaVu Sans Mono")
            prev = v
    ax.set_xlim(t0, t1)
    ax.set_ylim(-0.35, n - 0.05)
    ax.set_yticks([])
    for s in ("left", "right", "top"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color("#c8ced3")
    ax.tick_params(axis="x", labelsize=8, colors=INK)


def fig_fe_pipeline():
    path = os.path.join(SIM, "xsim_work_wave_fe", "wave.vcd")
    if not os.path.exists(path):
        print("缺 %s，先跑 run_xsim_wave.sh" % path)
        return
    names, data = parse_vcd(path)
    byname = {names[k][0].split(".")[-1]: (k, names[k][1]) for k in names}
    t_end = max(ch[-1][0] for ch in data.values() if ch)

    def sg(n):
        k, w = byname[n]
        return segs(data[k], t_end)

    ready = sg("din_ready")
    pulses = [a for (a, b, v) in ready if v == "1"]
    vld = [a for (a, b, v) in sg("dout_vld") if v == "1"]
    if len(pulses) < 6 or len(vld) < 6:
        print("脉冲太少（ready=%d vld=%d），检查测试台" % (len(pulses), len(vld)))
        return

    # 窗口放在流水灌满之后：群延迟 96 采样，前面那段 dout 全是 0
    k = min(150, len(pulses) - 5)
    t0 = (pulses[k] - 5e3) / 1000.0
    t1 = t0 + 4 * 30.0               # 4 个采样 = 120 ns

    def ns(s):
        return [(a / 1000.0, b / 1000.0, v) for (a, b, v) in s]

    def s16(v):
        return "%+d" % (v - 65536 if v > 32767 else v)

    rows = [
        ("clk", 1, ns(sg("clk")), None),
        ("din_vld", 1, ns(sg("din_vld")), None),
        ("din_ready", 1, ns(sg("din_ready")), None),
        ("din", 16, ns(sg("din")), s16),
        ("dout_vld", 1, ns(sg("dout_vld")), None),
        ("dout", 16, ns(sg("dout")), s16),
    ]

    # 单样本延迟：第 k 个 ready 收到的采样，在第 k 个 vld 出结果
    lat = (vld[k] - pulses[k]) / 1e4     # ps -> 拍（10 ns）

    fig, ax = plt.subplots(figsize=(9.4, 4.3))
    draw_panel(ax, rows, t0, t1)

    # 3 拍节奏：相邻两个 ready 之间的跨度
    y = 0.10
    x0, x1 = pulses[k] / 1000.0, pulses[k + 1] / 1000.0
    ax.annotate("", xy=(x0, y), xytext=(x1, y),
                arrowprops=dict(arrowstyle="<->", color=ACCENT, lw=1.2))
    ax.text((x0 + x1) / 2, y - 0.04, "3 拍 = 30 ns 收 1 个采样",
            ha="center", va="top", fontsize=8.5, color=ACCENT)

    ax.set_xlabel("时间 / ns（时钟 100 MHz，1 拍 = 10 ns）", fontsize=9, color=INK)
    ax.set_title("fir_mb_fe 前端流水：每 3 拍收一个采样，单样本 %d 拍后出结果（xsim）"
                 % round(lat), fontsize=11, color=INK, pad=8)
    fig.tight_layout()
    save(fig, "fig5_1_fe_pipeline.png")


# ---------------------------------------------------------------- 限幅
LIM_T = 22937


def lim16(u):
    if -LIM_T <= u <= LIM_T:
        return u
    e = abs(u) - LIM_T
    if e < 6554:
        c = e >> 1
    elif e < 9831:
        c = 3277 + ((e - 6554) >> 2)
    elif e < 26215:
        c = 4096 + ((e - 9831) >> 4)
    elif e < 75311:
        c = 5120 + ((e - 26215) >> 6)
    elif e < 466615:
        c = 5888 + ((e - 75311) >> 8)
    else:
        c = 7416 + ((e - 466615) >> 12)
    y = LIM_T + c
    return -y if u < 0 else y


def sat16(u):
    return 32767 if u > 32767 else (-32768 if u < -32768 else u)


def load_pairs(name):
    path = os.path.join(SIM, "xsim_work_wave_lim", name)
    if not os.path.exists(path):
        print("缺 %s，先跑 run_xsim_wave.sh" % path)
        return None
    u, y = [], []
    for ln in open(path):
        a, b = ln.split()
        u.append(int(a))
        y.append(int(b))
    return u, y


def fig_limiter():
    wave = load_pairs("lim_wave_inout.txt")
    curve = load_pairs("lim_curve.txt")
    if wave is None or curve is None:
        return
    u, y = curve
    xf = [v / FS for v in u]
    yf = [v / FS for v in y]

    fig = plt.figure(figsize=(11.2, 5.6))
    gs = fig.add_gridspec(2, 2, width_ratios=[1.0, 1.12],
                          height_ratios=[1.0, 1.0], hspace=0.62, wspace=0.26)
    a1 = fig.add_subplot(gs[0, 0])
    a2 = fig.add_subplot(gs[1, 0])
    a3 = fig.add_subplot(gs[:, 1])

    # ---------------------------------------------------------- (a) 膝部放大
    a1.plot(xf, yf, color=ACCENT, lw=2.0, label="六段折线软膝（实测）")
    lim = [max(-1.0, min(1.0, v)) for v in xf]
    a1.plot(xf, lim, color=GREY, lw=1.0, ls=(0, (4, 2)), label="改动前的硬饱和")
    a1.plot([-2.2, 2.2], [-2.2, 2.2], color="#c8ced3", lw=0.8, ls=":",
            label="不限幅 y = u")
    for s in (1, -1):
        a1.axvline(s * LIM_T / FS, color=BLUE, lw=0.9, ls=(0, (2, 2)))
    a1.text(LIM_T / FS + 0.05, -0.95, "门限 0.70 FS", fontsize=7.2, color=BLUE)

    # 拐点：0.70 / 0.90 / 1.00 / 1.50 FS
    for e in (LIM_T, LIM_T + 6554, LIM_T + 9831, LIM_T + 26215):
        for s in (1, -1):
            v = s * e
            if abs(v) / FS <= 2.2:
                a1.plot(v / FS, lim16(v) / FS, "o", ms=3.5, mfc="white",
                        mec=INK, mew=0.9)

    a1.set_xlim(-2.2, 2.2)
    a1.set_ylim(-1.1, 1.1)
    a1.set_xlabel("输入 u / FS", fontsize=9)
    a1.set_ylabel("输出 y / FS", fontsize=9)
    a1.set_title("(a) 膝部放大：0.70 FS 起逐段变平", fontsize=9.5)
    a1.grid(color="#e3e7ea", lw=0.6)
    a1.legend(fontsize=7.2, loc="upper left", frameon=False)
    a1.tick_params(labelsize=7.5)

    # ---------------------------------------------------------- (b) 分段斜率
    # 由实测点算：隔 8 个扫描点（Δu = 4096）做一次差分，把整数量化的抖动压掉
    W = 8
    su = [(u[i + W] + u[i]) / 2 / FS for i in range(len(u) - W)]
    sy = [(y[i + W] - y[i]) / (u[i + W] - u[i]) for i in range(len(u) - W)]
    a2.plot(su, sy, "o", ms=2.2, color=ACCENT, alpha=0.75, label="实测差分")

    edges = [0.0, LIM_T, LIM_T + 6554, LIM_T + 9831, LIM_T + 26215,
             LIM_T + 75311, 262144.0]
    slopes = [1, 0.5, 0.25, 1 / 16, 1 / 64, 1 / 256]
    xe, ye = [], []
    for i, s in enumerate(slopes):
        xe += [edges[i] / FS, edges[i + 1] / FS]
        ye += [s, s]
    a2.plot(xe, ye, color=INK, lw=1.0, drawstyle="steps-post", label="规则给出的分段斜率")

    names = ["1", "1/2", "1/4", "1/16", "1/64", "1/256"]
    lab_x = [0.30, 0.80, 1.12, 1.28, 2.25, 5.20]
    lab_k = [1.5, 1.35, 2.3, 2.6, 2.0, 1.6]
    for i, s in enumerate(slopes):
        a2.text(lab_x[i], s * lab_k[i], names[i], ha="center", va="bottom",
                fontsize=7.2, color=INK)
    for e in edges[1:-1]:
        a2.axvline(e / FS, color=BLUE, lw=0.7, ls=(0, (2, 3)), alpha=0.55)

    a2.text(5.6, 1 / 256 * 4.0, "一直平到 8 FS：8 FS 进、0.90 FS 出",
            ha="center", va="bottom", fontsize=7.2, color=ACCENT)
    a2.set_yscale("log")
    a2.set_ylim(1e-4, 2.4)
    a2.set_xlim(0, 8)
    a2.set_xlabel("输入 |u| / FS", fontsize=9)
    a2.set_ylabel("局部斜率 dy/du", fontsize=9)
    a2.set_title("(b) 每一段的斜率（对数纵轴）", fontsize=9.5)
    a2.grid(color="#e3e7ea", lw=0.6, which="both")
    a2.legend(fontsize=7.2, loc="lower left", frameon=False)
    a2.tick_params(labelsize=7.5)

    # ---------------------------------------------------------- (c) 波形
    uw, yw = wave
    t = list(range(len(uw)))
    a3.plot(t, [v / FS for v in uw], color=GREY, lw=1.1, label="输入 u")
    a3.plot(t, [v / FS for v in yw], color=ACCENT, lw=1.8, label="输出 y（软膝）")
    a3.plot(t, [sat16(v) / FS for v in uw], color=BLUE, lw=1.0,
            ls=(0, (4, 2)), label="输出（改动前，硬饱和）")
    for s in (1, -1):
        a3.axhline(s * LIM_T / FS, color=BLUE, lw=0.8, ls=(0, (2, 2)), alpha=0.6)
    a3.set_xlabel("采样点（周期 24，输入 2 FS 正弦）", fontsize=9)
    a3.set_ylabel("幅度 / FS", fontsize=9)
    a3.set_title("(c) 过载正弦：压扁而不是削平", fontsize=9.5)
    a3.grid(color="#e3e7ea", lw=0.6)
    a3.legend(fontsize=7.5, loc="upper right", frameon=False)
    a3.tick_params(labelsize=7.5)
    a3.set_xlim(0, len(t) - 1)

    fig.suptitle("末级软膝限幅（fir_drc4，xsim 逐点实测）", fontsize=11, y=0.97)
    fig.subplots_adjust(left=0.065, right=0.985, top=0.89, bottom=0.10)
    save(fig, "fig5_2_limiter.png")


def fig_i2s_frame():
    path = os.path.join(SIM, "xsim_work_wave_i2s", "wave.vcd")
    if not os.path.exists(path):
        print("缺 %s，先跑 run_xsim_wave.sh" % path)
        return
    names, data = parse_vcd(path)
    by = {names[k][0].split(".")[-1]: k for k in names}
    t_end = max(ch[-1][0] for ch in data.values() if ch)

    def us(n):
        return [(a / 1e6, b / 1e6, v)
                for (a, b, v) in segs(data[by[n]], t_end)]

    lr_ch = data[by["lrclk"]]
    edges = [t for i, (t, v) in enumerate(lr_ch[1:], 1)
             if v != lr_ch[i - 1][1] and v in ("0", "1")]
    if len(edges) < 6:
        print("LRCLK 边沿太少，检查 I2S 测试台")
        return

    k = min(10, len(edges) - 3)
    t0 = edges[k] / 1e6 - 2.0
    t1 = edges[k + 2] / 1e6          # 一个完整 L+R 帧

    rows = [
        ("lrclk", 1, us("lrclk"), None),
        ("bclk", 1, us("bclk"), None),
        ("sdata_o", 1, us("sdata_o"), None),
        ("sdata_i", 1, us("sdata_i"), None),
    ]

    fig, ax = plt.subplots(figsize=(10.2, 3.5))
    draw_panel(ax, rows, t0, t1)
    ax.set_ylim(-0.30, len(rows) + 0.02)

    # 从波形上把两个字解出来：跳过 LRCLK 边沿后的第一个 BCLK（I2S 约定），
    # 取 32 拍，24 位数据左对齐、尾部 8 拍补 0
    def bit_at(ch, t):
        v = ch[0][1]
        for tt, vv in ch:
            if tt <= t:
                v = vv
            else:
                break
        return v

    rises = [t for t, v in data[by["bclk"]] if v == "1"]
    words = []
    for kk in (k, k + 1):
        rs = [t for t in rises if edges[kk] < t < edges[kk + 1]]
        if len(rs) < 25:
            words.append(None)
            continue
        bits = "".join(bit_at(data[by["sdata_o"]], t) for t in rs[1:25])
        words.append(int(bits, 2))

    lw = "0x%06X" % words[0] if words[0] is not None else "?"
    rw = "0x%06X" % words[1] if words[1] is not None else "?"

    # 声道标注
    mid_l = (edges[k] / 1e6 + edges[k + 1] / 1e6) / 2
    mid_r = (edges[k + 1] / 1e6 + edges[k + 2] / 1e6) / 2
    top = len(rows) - 0.05
    ax.text(mid_l, top, "左声道 = %s" % lw, ha="center", va="bottom",
            fontsize=8.5, color=ACCENT)
    ax.text(mid_r, top, "右声道 = %s" % rw, ha="center", va="bottom",
            fontsize=8.5, color=ACCENT)

    # I2S 的特点：MSB 比声道边沿晚一个 BCLK
    ax.text(t0 + 0.3, 2.0, "MSB 比 LRCLK 边沿晚 1 个 BCLK；24 位左对齐，尾部补 8 拍 0",
            ha="left", va="center", fontsize=7.5, color=BLUE)

    ax.set_xlabel("时间 / µs（一帧 20.5 µs，48 kHz）", fontsize=9, color=INK)
    ax.set_title("自写 I2S 主端：LRCLK / BCLK / 两路数据（xsim，codec 模型对端）",
                 fontsize=11, color=INK, pad=10)
    fig.tight_layout()
    save(fig, "fig5_3_i2s_frame.png")


if __name__ == "__main__":
    fig_fe_pipeline()
    fig_limiter()
    fig_i2s_frame()
