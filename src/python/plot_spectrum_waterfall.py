"""Create a 3D time-frequency view from the generated multiband WAV output.

Run after multiband_baseline.py from the repository root:
    python src/python/plot_spectrum_waterfall.py
"""

import argparse
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import soundfile as sf
from scipy import signal


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="data/audio/multiband_output.wav")
    parser.add_argument("--output", default="data/figures/spectrum_waterfall.png")
    parser.add_argument("--max-hz", type=float, default=8000.0)
    args = parser.parse_args()

    x, fs = sf.read(args.input)
    if x.ndim > 1:
        x = np.mean(x, axis=1)
    if len(x) < 1024:
        raise SystemExit("Input audio must contain at least 1024 samples.")

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
    magnitude_db = 20.0 * np.log10(np.maximum(spectrum[keep], 1e-8))

    # Limit the mesh size while retaining the shape of a long recording.
    time_step = max(1, len(times) // 180)
    times = times[::time_step]
    magnitude_db = magnitude_db[:, ::time_step]
    time_grid, freq_grid = np.meshgrid(times, frequencies)

    fig = plt.figure(figsize=(12, 7))
    ax = fig.add_subplot(111, projection="3d")
    surface = ax.plot_surface(
        time_grid,
        freq_grid,
        magnitude_db,
        cmap="viridis",
        linewidth=0,
        antialiased=True,
        rstride=2,
        cstride=2,
    )
    ax.set_title("Processed Audio Spectrum Over Time")
    ax.set_xlabel("Time (s)", labelpad=9)
    ax.set_ylabel("Frequency (Hz)", labelpad=10)
    ax.set_zlabel("Magnitude (dB)", labelpad=8)
    ax.view_init(elev=35, azim=-58)
    fig.colorbar(surface, ax=ax, shrink=0.62, pad=0.1, label="Magnitude (dB)")
    fig.tight_layout()
    output_dir = os.path.dirname(args.output)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    fig.savefig(args.output, dpi=160)
    plt.close(fig)
    print(f"Saved {args.output} ({fs} Hz, {len(x)} samples)")


if __name__ == "__main__":
    main()
