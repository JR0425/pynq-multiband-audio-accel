# PYNQ-Z2 Multiband Audio Accelerator

A PYNQ-Z2 project for a multiband dynamic-range-compression (DRC) audio pipeline. The repository contains a Python reference implementation, an HLS FIR core and its C-simulation flow, board-side playback and verification utilities, and measurements from both a reference overlay and the custom HLS core.

## Project status

- The Python reference baseline uses 48 kHz, four subtractive bands, and 193-tap linear-phase low-pass filters. Its current local run is recorded in `data/results/baseline_metrics.md`.
- The custom HLS core has out-of-context synthesis and implementation results in `data/results/impl_metrics.md`. These results describe the core, not the integrated audio system.
- A separate open-source reference overlay was tested on the board. Its results are in `data/results/reference_overlay_metrics.md` and do not represent the custom core.
- The custom HLS core has not yet been integrated into a project overlay and measured end to end on the board. RTL and the Python/HLS/RTL three-way comparison are also pending.

## Repository layout

| Directory | Contents |
| --- | --- |
| `src/python/` | Python baseline, coefficient and test-data exporters, comparison and analysis utilities |
| `src/hls/` | HLS core and C testbench |
| `src/rtl/` | Reserved for RTL implementation; no custom RTL source is present yet |
| `sim/hls_csim/` | FIR coefficient files used by C simulation |
| `build/hls/` | HLS and Vivado scripts, directives, and reports |
| `board/` | PYNQ notebooks, board scripts, and the reference overlay files |
| `data/audio/` | Test audio and generated audio |
| `data/figures/` | Generated analysis figures |
| `data/results/` | Software, simulation, and implementation records |
| `skill/` | Prompts, templates, checkers, and troubleshooting notes |
| `report/` | Design report, interface notes, submission checklist, and collaboration logs |

## Python baseline

Use Python 3.9 or newer. From the repository root in PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-python.txt
python src/python/multiband_baseline.py --taps 193 --repeat 5
```

The input is `data/audio/real_voice.wav`. The script resamples it to 48 kHz, runs the four-band DRC reference, and writes `data/audio/multiband_output.wav` and `data/figures/multiband_comparison.png`. To create a time-frequency waterfall from that output, run `python src/python/plot_spectrum_waterfall.py`; it writes `data/figures/spectrum_waterfall.png`. The printed CPU timing is a local host measurement. It is not a board-side speedup result.

To regenerate coefficients and the floating-point reference for the current HLS test input:

```powershell
python src/python/export_coefficients.py --taps 193
python src/python/export_golden.py --taps 193
```

## HLS C simulation

The project C-simulation script targets the Vivado/Vitis HLS installation documented in `build/hls/run_csim.tcl`. On a Windows machine with that toolchain installed, run the documented batch command from the repository root, then compare `data/results/hw_output.txt` with `data/results/python_golden.txt`:

```powershell
$env:MSYS_NO_PATHCONV = '1'
cmd /c "E:\Xilinx\Vitis_HLS\2020.2\bin\vitis_hls.bat -f build\hls\run_csim.tcl"
python src/python/compare_golden_vs_hw.py
```

The comparison command prints SNR, maximum absolute error, sample alignment, and the transparent-mode bit-exact result. When valid HLS output files are present, it also saves `data/figures/hls_golden_comparison.png`.

Confirm the installed tool version and paths before running. The C-simulation Tcl regenerates its output files, so retain any results that need to be compared before starting another run.

## Board work and measurements

The notebooks and scripts in `board/` include audio playback and overlay checks. The `ps_only` bitstream is a board-side playback/reference artifact; it does not contain the custom multiband HLS core. Read `data/results/reference_overlay_metrics.md` and `data/results/impl_metrics.md` for the scope and limitations of each measurement.

## License

MIT. See `LICENSE`.
