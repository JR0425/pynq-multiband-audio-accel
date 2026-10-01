"""把硬件（或 HLS C 仿真）的输出与 Python 黄金参考逐点比对。

它回答两个问题：
    1. **硬件算的，和理论上应该算出来的，是不是同一个东西**（SNR / 相关系数）
    2. **结构搭对了没有**（直通检验：不做压缩时输出应当逐位等于延迟 D 拍的输入）

第 2 项比第 1 项硬得多。SNR 是"差多少 dB"，它只会告诉你"不够好"；
而逐位比对是"对不对"，对不上就是结构错了 —— 两件事必须分开，
否则会拿"量化误差大"去解释一个其实是接错了的问题。

当前脚本的验收判据是 **SNR ≥ 70 dB**。这个数不是拍的：
采样 16 位本身的理论上限约 96 dB，本项目只要求"助听器听起来干净"，
70 dB 已远高于这个要求。

用法（在仓库根目录）：
    python src/python/compare_golden_vs_hw.py
退出码 0 = 通过，1 = 不通过。
"""

import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

GOLDEN = "data/results/python_golden.txt"
HW = "data/results/hw_output.txt"
TRANSPARENT = "data/results/hw_transparent_i16.txt"
INPUT = "data/audio/test_input.txt"
PLOT = "data/figures/hls_golden_comparison.png"

SNR_MIN_DB = 70.0
MAX_ABS_ERROR = 1e-3
LAG_RANGE = 16          # 位移搜索范围。正常情况下最佳位移应当是 0。


def best_lag(ref, hw, span):
    """在 -span..span 里找使相关系数最大的位移。返回 (位移, 该位移下的相关系数)。"""
    best = (0, -2.0)
    for lag in range(-span, span + 1):
        if lag >= 0:
            a, b = ref[lag:], hw[: len(hw) - lag] if lag else hw
        else:
            a, b = ref[: len(ref) + lag], hw[-lag:]
        if len(a) < 32 or np.std(a) < 1e-12 or np.std(b) < 1e-12:
            continue
        c = float(np.corrcoef(a, b)[0, 1])
        if c > best[1]:
            best = (lag, c)
    return best


def snr_db(ref, hw):
    """信噪比：把 (硬件 − 参考) 当成噪声，参考当成信号。"""
    noise = float(np.sum((hw - ref) ** 2))
    if noise <= 0.0:
        return float("inf")
    return 10.0 * np.log10(float(np.sum(ref ** 2)) / noise)


def compare_main():
    try:
        ref = np.loadtxt(GOLDEN, dtype=np.float64)
        hw = np.loadtxt(HW, dtype=np.float64)
    except OSError as exc:
        print(f"读不到文件：{exc}")
        print(f"  黄金参考 {GOLDEN} —— 跑 src/python/export_golden.py")
        print(f"  硬件输出 {HW} —— 跑 build/hls/run_csim.tcl")
        return None, False

    if len(ref) != len(hw):
        print(f"长度不一致：黄金 {len(ref)}，硬件 {len(hw)} —— 直接判不通过")
        return None, False

    err = hw - ref
    mse = float(np.mean(err ** 2))
    max_err = float(np.max(np.abs(err)))
    denom = float(np.sqrt(np.sum(ref ** 2) * np.sum(hw ** 2)))
    correlation = float(np.sum(ref * hw) / denom) if denom > 0 else 1.0
    snr = snr_db(ref, hw)
    lag, lag_corr = best_lag(ref, hw, LAG_RANGE)

    print("=" * 60)
    print("一、与黄金参考比对（SNR 是验收判据）")
    print(f"  样本数            {len(ref)}")
    print(f"  信噪比 SNR        {snr:.1f} dB   （判据 ≥ {SNR_MIN_DB:.0f} dB）")
    print(f"  相关系数          {correlation:.9f}")
    print(f"  均方误差 MSE      {mse:.3e}")
    print(f"  最大单点误差      {max_err:.3e}   （判据 ≤ {MAX_ABS_ERROR:.0e}）")
    print(f"  最佳位移          {lag:+d}  (该位移下相关系数 {lag_corr:.9f})")
    print(f"  参考 max|y|       {np.max(np.abs(ref)):.6f}")
    print(f"  硬件 max|y|       {np.max(np.abs(hw)):.6f}")

    # Keep a visual record of the same numeric comparison. This is a C-sim/HLS
    # output comparison, not a board measurement.
    os.makedirs(os.path.dirname(PLOT), exist_ok=True)
    sample = np.arange(len(ref))
    fig, (ax_out, ax_err) = plt.subplots(
        2, 1, figsize=(10, 6), sharex=True,
        gridspec_kw={"height_ratios": [2, 1]},
    )
    ax_out.plot(sample, ref, label="Python golden", linewidth=1.4)
    ax_out.plot(sample, hw, label="HLS output", linewidth=1.0, alpha=0.8)
    ax_out.set_ylabel("Amplitude")
    ax_out.set_title(f"HLS C simulation vs Python golden ({snr:.1f} dB SNR)")
    ax_out.grid(True, alpha=0.25)
    ax_out.legend()
    ax_err.plot(sample, err, color="#b24a32", linewidth=1.0)
    ax_err.set_xlabel("Sample")
    ax_err.set_ylabel("HLS − ref")
    ax_err.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(PLOT, dpi=160)
    plt.close(fig)
    print(f"  比对图            {PLOT}")

    ok = True
    if lag != 0:
        print(f"!! 最佳位移是 {lag:+d} 而不是 0：两边的延迟结构不一致，先查这个，别急着看误差。")
        ok = False
    if snr < SNR_MIN_DB:
        print(f"!! SNR {snr:.1f} dB 低于判据 {SNR_MIN_DB:.0f} dB")
        ok = False
    if max_err > MAX_ABS_ERROR:
        print(f"!! 最大单点误差 {max_err:.3e} 超过判据 {MAX_ABS_ERROR:.0e}")
        ok = False
    print("  小结：" + ("通过。" if ok else "不通过，见上面的 !! 行。"))
    return snr, ok


def transparent_main():
    """直通检验：压缩比设成 1.0（等于不压缩）时，输出应当逐位等于延迟 D 拍的输入。

    相减式结构各段之和恒等于延迟 D 拍的输入，所以这一项**必须精确成立**。
    它同时验三件事：频段相减接对了、第 4 段用的 hist[D] 延迟对、入出的 Q1.15 没差 2 的幂。
    """
    if not os.path.exists(TRANSPARENT):
        print(f"\n（跳过直通检验：没有 {TRANSPARENT}）")
        return True

    x = np.loadtxt(INPUT, dtype=np.float64)
    xq = np.clip(np.round(x * 32768.0), -32768, 32767).astype(np.int64)
    t = np.loadtxt(TRANSPARENT, dtype=np.int64)

    if len(t) != len(xq):
        print(f"\n!! 直通检验：长度不一致 {len(t)} vs {len(xq)}")
        return False

    # 找错配最少的位移。不预设 D —— 让它自己报出来，才能发现"延迟量算错了"。
    best_lag, best_bad = -1, None
    for lag in range(0, min(len(xq), 512)):
        bad = int(np.sum(t[lag:] != xq[: len(xq) - lag]))
        if best_bad is None or bad < best_bad:
            best_lag, best_bad = lag, bad

    print("\n二、直通检验（压缩比 = 1.0，输出应逐位等于延迟 D 拍的输入）")
    print(f"  最佳位移 {best_lag}   错配 {best_bad} / {len(xq)} 个采样")

    if best_bad == 0:
        print(f"  结论：通过 —— 群延迟 D = {best_lag}，逐位一致。")
        return True

    # 允许的非零错配只有一种来源：输入恰好到满量程 ±1.0，
    # 而阈值 32767/32768 = 0.99997 挡不住它，于是落进了压缩支路。
    idx = np.nonzero(t[best_lag:] != xq[: len(xq) - best_lag])[0]
    vals = np.unique(xq[idx])
    print(f"  错配处的输入取值：{vals[:8]}（共 {len(vals)} 种）")
    if np.all(np.abs(vals) >= 32767):
        print("  结论：通过 —— 全部错配都发生在输入满量程处（阈值挡不住 ±1.0），"
              "不是结构问题。")
        return True
    print("  结论：不通过 —— 有非满量程的错配，说明结构或延迟对不上。")
    return False


def main():
    snr, ok = compare_main()
    ok2 = transparent_main()
    print("\n" + "=" * 60)
    print("总结论：" + ("通过。" if (ok and ok2) else "不通过。"))
    return 0 if (ok and ok2) else 1


if __name__ == "__main__":
    sys.exit(main())
