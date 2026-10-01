# Board Artifacts and Status

## Files in this repository

- `overlay/fir.bit` and `overlay/fir.hwh`: the project overlay — the custom multiband HLS core integrated with the PYNQ audio codec. Built by `overlay/build_fir.tcl`.
- `overlay/fir.hwdef`, `overlay/fir_timing.rpt`, `overlay/fir_util.rpt`: block-design source and the post-implementation timing and resource reports for that overlay.
- `overlay/ps_only.bit` and its `.hwh` metadata: an earlier playback-only overlay artifact. It does not contain the custom core.
- `overlay/audio.bit` and its `.hwh` metadata: the audio-only milestone overlay — a project-built bitstream that drives the audio codec, without the accelerator core. Built by `overlay/build_audio.tcl`; loaded by `notebooks/w3_own_overlay_audio.ipynb` and `scripts/verify_own_overlay_audio.py`.
- `scripts/fir_core.py`: register-level driver for the core (AXI-Lite control, AXI4-Master data movement).
- `scripts/fir_audio_loop.py`: microphone → core → headphone loop, one block at a time.
- `scripts/fir_selftest.py`: bypass and block-continuity checks.
- `scripts/compare_cpu_fpga.py`: same-board ARM baseline against the core.
- `notebooks/w3_own_overlay_audio.ipynb`: loads `overlay/audio.bit`, the audio-only milestone build that has no accelerator core, and confirms that a project-built bitstream can drive the audio codec.
- `notebooks/w2_audio_playback.ipynb`: base-overlay audio playback milestone notebook.
- `notebooks/test_multiband.ipynb`: board-side multiband test notebook; confirm its overlay interface against the final design before use.
- `scripts/verify_audio_playback.py`: base-overlay audio playback and capture check.
- `scripts/test_overlay_load.py`: overlay load and IP inventory check.
- `scripts/verify_fir_hw_e2e.py`: DMA FIR test procedure for an overlay exposing the expected FIR DMA. Its default `fir_accel2.bit` and `filter.fir_dma` are not included in this repository.

## Evidence boundary

The custom-core board results are `data/results/fir_audio_loop_run_20261001.txt` and `data/results/compare_cpu_fpga_run_20261001_fix.txt`; the write-up is `data/results/accel_cpu_vs_fpga.md`. The core ran on real captured audio (144,000 samples in 18 blocks) bit-exact against the reference, and the same-board ARM baseline measured 6.116 µs against the core's 4.507 µs per sample.

`data/results/reference_overlay_metrics.md` records a different, earlier test: a separate open-source 27-tap FIR reference overlay. That result validates a board-side DMA path only and does not measure the custom core. Keep the two apart when quoting numbers.

Before running an overlay test, confirm the bitstream, `.hwh`, DMA/IP names, board image version, and expected sample format as a set. Do not substitute `ps_only.bit` into `verify_fir_hw_e2e.py`; it does not expose the FIR DMA expected by that script.
