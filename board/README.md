# Board Artifacts and Status

## Files in this repository

- `overlay/fir.bit` and `overlay/fir.hwh`: the project overlay — the custom multiband HLS core integrated with the PYNQ audio codec. Built by `overlay/build_fir.tcl`.
- `overlay/fir.hwdef`, `overlay/fir_timing.rpt`, `overlay/fir_util.rpt`: block-design source and the post-implementation timing and resource reports for that overlay.
- `overlay/ps_only.bit` and its `.hwh` metadata: an earlier playback-only overlay artifact. It does not contain the custom core.
- `overlay/audio.bit` and its `.hwh` metadata: the audio-only milestone overlay — a project-built bitstream that drives the audio codec, without the accelerator core. Built by `overlay/build_audio.tcl`; loaded by `notebooks/w3_own_overlay_audio.ipynb` and `scripts/verify_own_overlay_audio.py`.
- `scripts/fir_core.py`: register-level driver for the core (AXI-Lite control, AXI4-Master data movement).
- `scripts/fir_audio_loop.py`: microphone → core → headphone loop, one block at a time. This is still the block-based path, not the real-time one.
- `scripts/fir_live.py`: microphone → core → headphone **real-time** path (record and play at once). Verified over continuous 8/10/24-second runs, all at 1.00× with no dropped block; the 24-second run moved 2,401 blocks and passed a 960,000/960,000-sample bit-exact self-check. The core interface was not changed and `fir.bit` was not rebuilt.
- `scripts/audio_stream.cpp` and `scripts/build_audio_stream.sh`: a self-written streaming audio path, compiled on the board with gcc.
- `scripts/audio_stream_test.py`, `scripts/audio_overhead_probe.py`, `scripts/audio_duplex_probe.py`: tests and probes for that streaming path.
- `scripts/run_live.sh`: one command to start the real-time path.
- `scripts/fir_chunk_sweep.py`: block-size sweep for the core.
- `scripts/fir_selftest.py`: bypass and block-continuity checks.
- `scripts/compare_cpu_fpga.py`: same-board ARM baseline against the core.
- `notebooks/w3_own_overlay_audio.ipynb`: loads `overlay/audio.bit`, the audio-only milestone build that has no accelerator core, and confirms that a project-built bitstream can drive the audio codec.
- `notebooks/w2_audio_playback.ipynb`: base-overlay audio playback milestone notebook.
- `notebooks/test_multiband.ipynb`: board-side smoke test for the integrated `fir.bit` overlay; it uses the current `fir_core.py` register driver and verifies bypass delay and compression on a synthetic signal.
- `scripts/verify_audio_playback.py`: base-overlay audio playback and capture check.
- `scripts/test_overlay_load.py`: overlay load and IP inventory check.
- `scripts/verify_fir_hw_e2e.py`: DMA FIR test procedure for an overlay exposing the expected FIR DMA. Its default `fir_accel2.bit` and `filter.fir_dma` are not included in this repository.

## Evidence boundary

The custom-core board results are `data/results/fir_audio_loop_run_20261001.txt` and `data/results/compare_cpu_fpga_run_20261001_fix.txt`; the write-up is `data/results/accel_cpu_vs_fpga.md`. The recorded 144,000-sample, 18-block capture passed the bypass identity check with zero mismatches, but its input level was nearly silent (effective value 1,342), so it does not prove an audible microphone A/B compression demo. A synthetic signal did demonstrate compression. A separate microphone → core → headphone real-time run (`scripts/fir_live.py`) ran 8/10/24 continuous seconds at 1.00× with no dropped block and passed a 960,000/960,000-sample bit-exact self-check; the core interface was not changed and `fir.bit` was not rebuilt. The same-board ARM-vs-core comparison is ⛔ stale: `scripts/compare_cpu_fpga.py` has been changed but has not yet been re-run on the board, so no current per-sample figure or speed-up ratio is available — do not quote the earlier 1.4× number. The matching comparison is ARM float64/scipy against PL Q1.15; it does not compare with a hand-written NEON CPU implementation.

`data/results/reference_overlay_metrics.md` records a different, earlier test: a separate open-source 27-tap FIR reference overlay. That result validates a board-side DMA path only and does not measure the custom core. Keep the two apart when quoting numbers.

Before running an overlay test, confirm the bitstream, `.hwh`, DMA/IP names, board image version, and expected sample format as a set. Do not substitute `ps_only.bit` into `verify_fir_hw_e2e.py`; it does not expose the FIR DMA expected by that script.
