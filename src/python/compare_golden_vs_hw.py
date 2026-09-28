"""把硬件（或 HLS C 仿真）的输出与 Python 黄金参考逐点比对。

它回答一个问题：**硬件算的，和理论上应该算出来的，是不是同一个东西。**

输出的四项误差指标是设计报告里要填的数据，所以数字都按可复现的方式给全。

为什么要先扫位移、再看最佳位移：
    逐点直接比对，只要两边差了一点点相位，得到的相关系数就会很低，
    **看起来像"算错了"，其实只是错开了**。
    所以脚本先在一段位移范围内扫一遍，报出最佳位移是多少。
    最佳位移应该等于滤波器的群延迟差 —— 如果它不等于 0，说明两边的
    延迟结构不一致，得先查清楚再谈误差。
    （FIR 的线性相位群延迟 = (抽头数-1)/2，本项目 65 抽头 = 32。）

用法（在仓库根目录）：
    python src/python/compare_golden_vs_hw.py

输出：屏幕上的比对报告；退出码 0 = 通过，1 = 不通过。
"""

import sys

import numpy as np

GOLDEN = "data/results/python_golden.txt"
HW = "data/results/hw_output.txt"

# 通过判据：相关系数要够高，且最大单点误差不超过这个值。
# 当前 HLS 核用 float32、黄金参考用 float64，差异只应来自浮点舍入，
# 实测在 1e-5 量级；留到 1e-3 是给后面的定点版本留余地。
MIN_CORRELATION = 0.999
MAX_ABS_ERROR = 1e-3
LAG_RANGE = 16


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


def main():
    try:
        ref = np.loadtxt(GOLDEN, dtype=np.float64)
        hw = np.loadtxt(HW, dtype=np.float64)
    except OSError as exc:
        print(f"读不到文件：{exc}")
        print(f"  黄金参考 {GOLDEN} —— 跑 src/python/export_golden.py 生成")
        print(f"  硬件输出 {HW} —— 跑 build/hls/run_csim.tcl 生成")
        return 1

    if len(ref) != len(hw):
        print(f"长度不一致：黄金 {len(ref)}，硬件 {len(hw)} —— 直接判不通过")
        return 1

    err = hw - ref
    mse = float(np.mean(err ** 2))
    mae = float(np.mean(np.abs(err)))
    max_err = float(np.max(np.abs(err)))
    denom = float(np.sqrt(np.sum(ref ** 2) * np.sum(hw ** 2)))
    correlation = float(np.sum(ref * hw) / denom) if denom > 0 else 1.0

    lag, lag_corr = best_lag(ref, hw, LAG_RANGE)

    print("=" * 56)
    print("软硬件结果比对")
    print(f"  样本数            {len(ref)}")
    print(f"  均方误差 MSE      {mse:.3e}")
    print(f"  平均绝对误差 MAE  {mae:.3e}")
    print(f"  最大单点误差      {max_err:.3e}")
    print(f"  相关系数          {correlation:.9f}")
    print(f"  最佳位移          {lag:+d}  (该位移下相关系数 {lag_corr:.9f})")
    print(f"  参考 max|y|       {np.max(np.abs(ref)):.6f}")
    print(f"  硬件 max|y|       {np.max(np.abs(hw)):.6f}")
    print("=" * 56)

    ok = True
    if lag != 0:
        print(f"!! 最佳位移是 {lag:+d} 而不是 0：两边的延迟结构不一致，先查这个，别急着看误差。")
        ok = False
    if correlation < MIN_CORRELATION:
        print(f"!! 相关系数 {correlation:.6f} 低于判据 {MIN_CORRELATION}")
        ok = False
    if max_err > MAX_ABS_ERROR:
        print(f"!! 最大单点误差 {max_err:.3e} 超过判据 {MAX_ABS_ERROR:.0e}")
        ok = False

    print("结论：通过 —— 硬件输出与黄金参考一致。" if ok else "结论：不通过，见上面的 !! 行。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
