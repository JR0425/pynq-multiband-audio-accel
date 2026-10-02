"""Create a 3D time-frequency view from the generated multiband WAV output.

Run after multiband_baseline.py from the repository root:
    python src/python/plot_spectrum_waterfall.py

纵轴单位是 **dBFS**（满量程正弦 = 0 dB），不是裸的 20*log10(|X|)。
两处都是 2026-10-02 改的：

  1. 裸 dB 没有绝对刻度 —— 读图的人不知道 0 在哪，"最大 −11.7 dB"这种话
     也写不进报告。换成 dBFS 之后可以直接说"峰值 −60 dBFS"。
  2. 色标原来让 matplotlib 按数据 min/max 自动定。数据里最深的点被 1e-8
     的截断下限压到 −160 dB，于是色标下面一半全浪费，画面主体挤在上半段、
     糊成一片黄绿。现在按"峰值往下铺 N dB"定。

另外切掉开头那段纯数字静音：它和后面的信号差约 100 dB，
在 3D 视角下是一道竖直的墙，正好挡在画面正前方。
"""

import argparse
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MaxNLocator
from scipy import signal

try:
    import soundfile as sf
except ImportError:
    # 本机 conda 环境里没装 soundfile（仓库其它脚本也做了同样的回退）。
    # 不写这个回退的话，这个脚本在这台机器上直接 ImportError 跑不起来。
    sf = None


def read_wav(path):
    """返回 (float 数组, 采样率)。soundfile 优先，没有就退回 scipy。"""
    if sf is not None:
        return sf.read(path)
    from scipy.io import wavfile

    fs, data = wavfile.read(path)
    return data.astype(np.float64) / 32768.0, fs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/audio/multiband_output.wav")
    parser.add_argument("--output", default="data/figures/spectrum_waterfall.png")
    parser.add_argument("--max-hz", type=float, default=8000.0)
    parser.add_argument("--db-range", type=float, default=60.0,
                        help="色标从峰值往下铺多少 dB")
    args = parser.parse_args()

    x, fs = read_wav(args.input)
    if x.ndim > 1:
        x = np.mean(x, axis=1)

    # 切掉开头的纯静音（见文件头说明）。记下切了多少，写进坐标轴标签。
    nonzero = np.abs(x) > 1e-6
    lead = int(np.argmax(nonzero)) if nonzero.any() else 0
    x = x[lead:]
    if len(x) < 1024:
        raise SystemExit("Input audio must contain at least 1024 non-silent samples.")

    frequencies, times, spectrum = signal.spectrogram(
        x,
        fs=fs,
        window="hann",
        nperseg=1024,
        noverlap=768,
        detrend=False,
        scaling="spectrum",
        mode="magnitude",
    )
    keep = frequencies <= min(args.max_hz, fs / 2)
    frequencies = frequencies[keep]

    # 换成 dBFS：一个满量程正弦经过这套换算正好是 0 dB。
    # 参考值就是"幅度 1.0 的正弦在 Hann 窗下的峰值格"= 0.5 * sum(win)。
    full_scale = 0.5 * np.hanning(1024).sum()
    magnitude_db = 20.0 * np.log10(np.maximum(spectrum[keep] / full_scale, 1e-9))

    # Limit the mesh size while retaining the shape of a long recording.
    time_step = max(1, len(times) // 180)
    times = times[::time_step]
    magnitude_db = magnitude_db[:, ::time_step]
    time_grid, freq_grid = np.meshgrid(times, frequencies)

    peak = float(magnitude_db.max())
    vmin = peak - args.db_range

    # 几何也要按同一个下限裁一刀，不能只裁颜色。
    # 噪声底那些向下的尖刺（真实低到 −160 dB）会在 3D 里竖成一堵墙，
    # 正好挡在时间轴前面，把刻度全遮掉 —— 只给 vmin/vmax 是管不住几何的。
    # 裁完之后底面是个干净的平面，山脊看得清，轴也露得出来。
    plot_db = np.maximum(magnitude_db, vmin)

    fig = plt.figure(figsize=(12, 7))
    ax = fig.add_subplot(111, projection="3d")
    surface = ax.plot_surface(
        time_grid,
        freq_grid,
        plot_db,
        cmap="viridis",
        linewidth=0,
        antialiased=True,
        rstride=2,
        cstride=2,
        vmin=vmin,
        vmax=peak,
    )
    ax.set_title("Processed Audio Spectrum Over Time (peak %.1f dBFS)" % peak)
    ax.set_xlabel("Time (s, from first non-silent sample)", labelpad=16)
    ax.set_ylabel("Frequency (Hz)", labelpad=12)
    # z 轴不写字：右边那根 colorbar 是同一段刻度、同一个量，已经带了
    # "Magnitude (dBFS)" 的标签。两边都写反而打架 —— z 标签的 labelpad 是
    # 往远离轴的方向推，推小了压在刻度数字上、推大了撞进 colorbar，没有落点。
    ax.set_zlabel("")
    ax.set_zlim(vmin, peak + 3.0)
    # 刻度别太密 —— 密了在 3D 里互相压，读不出来。
    ax.xaxis.set_major_locator(MaxNLocator(6))
    ax.yaxis.set_major_locator(MaxNLocator(6))
    ax.set_box_aspect(None, zoom=1.12)
    ax.view_init(elev=26, azim=-62)
    fig.colorbar(surface, ax=ax, shrink=0.62, pad=0.06, label="Magnitude (dBFS)")
    # 3D 轴用 tight_layout() 不收敛（它算不准 3D 的边距），
    # 会把 x 轴标签挤出画布 —— 实测就是这么被切掉半个字的。
    # bottom 留 0.1 是给那行 x 轴标签的位置，少了就切掉尾巴。
    fig.subplots_adjust(left=0.01, right=0.88, top=0.93, bottom=0.10)
    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    fig.savefig(args.output, dpi=160)
    plt.close(fig)
    print("Saved %s (%d Hz, %d samples, skipped %d leading samples, "
          "peak %.1f dBFS, colour range %.0f..%.0f dBFS)"
          % (args.output, fs, len(x), lead, peak, vmin, peak))


if __name__ == "__main__":
    main()
