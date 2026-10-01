# Python Software Baseline

## Current reference run

Run date: 2026-10-01

Command: `python src/python/multiband_baseline.py --taps 193 --repeat 5`

Environment: repository virtual environment, Python 3.9.12, NumPy 2.0.2, SciPy 1.13.1, Matplotlib 3.9.4, SoundFile 0.13.1. The CPU model was not available from the current Windows session, so retain the machine-specific timing as a local reference rather than a portable benchmark.

| Item | Result |
| --- | ---: |
| Input | `data/audio/real_voice.wav`, stereo, 44.1 kHz, about 10.58 s |
| Processing rate | 48 kHz after polyphase resampling |
| Test length after resampling | 507,905 samples |
| Filter design | 3 low-pass FIRs at 500, 1,000, and 2,000 Hz; 193 taps each |
| Output bands | 4 subtractive bands, followed by DRC |
| Group delay | 96 samples, 2.00 ms at 48 kHz |
| Reconstruction check before DRC | Maximum coefficient-sum error `3.469e-18` |
| Best of 5 processing times (run A) | 100.7 ms |
| Host time per sample (run A) | 0.198 µs/sample |
| Host real-time factor (run A) | 105.1× |
| Best of 5 processing times (run B) | 89.5 ms |
| Host time per sample (run B) | 0.176 µs/sample |
| Host real-time factor (run B) | 118.3× |
| HLS core schedule reference | 449 cycles/sample at 100 MHz = 4.49 µs/sample; 4.6× against the 48 kHz sample budget |

The Python and HLS values are not a measured end-to-end speedup comparison. The Python number comes from the local host; the HLS figure comes from a core-level schedule and out-of-context implementation. The custom HLS core has not been integrated and timed through the board audio path. In both runs the host processed each sample faster than the HLS schedule figure, so do not claim that the FPGA is faster from these measurements. The poster draft displays run A as one measured example.

The repository's existing 1,000-sample HLS output was compared with the freshly regenerated Python golden output. The numeric comparison passed: SNR 75.4 dB, correlation 0.999999986, MSE `6.810e-10`, maximum absolute error `3.469e-05`, and best lag 0. The stored transparent-mode output also matched the quantized test input bit for bit at a 96-sample delay (0 mismatches out of 1,000). This update reran the comparison, not Vitis HLS itself; the HLS executable is unavailable at the documented path on this workstation. Rebuild C simulation before treating the result as a reproducible clean-toolchain run.

Generated artifacts:

- `data/audio/multiband_output.wav`
- `data/figures/multiband_comparison.png`
- `data/figures/spectrum_waterfall.png`
- `data/figures/hls_golden_comparison.png`
- `data/results/python_golden.txt` (separate 1,000-sample HLS test vector)

Reproduce the software run from the repository root with the command above. Timing varies with host load and hardware.

## Historical baseline (2026-09-25)

The table below is retained as a historical record of the superseded design: 44.1 kHz input without hardware-rate resampling, 65 taps, and independently designed bands. It is not comparable to the current configuration and must not be used as current software performance data.

| Test | Historical parameters | Recorded time | Notes |
| --- | --- | ---: | --- |
| Input read and DC removal | `mean(x)` | about 0.05 s | Historical run |
| Single low-pass FIR | 65 taps, 600 Hz | about 0.12 s | Historical run |
| Four-band processing | 65 taps × 4 | about 0.35 s | Superseded architecture |
| DRC | threshold 0.1, ratio 0.7 | about 0.08 s | Historical run |
| Full run including FFT plot | old pipeline | about 1.2 s | Not comparable to current run |
