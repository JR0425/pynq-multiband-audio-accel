# Board Artifacts and Status

## Files in this repository

- `overlay/fir.bit` and `overlay/fir.hwh`: the project overlay — the custom multiband HLS core integrated with the PYNQ audio codec. Built by `overlay/build_fir.tcl`.
- `overlay/fir.hwdef`, `overlay/fir_timing.rpt`, `overlay/fir_util.rpt`: block-design source and the post-implementation timing and resource reports for that overlay.
- `overlay/ps_only.bit` and its `.hwh` metadata: an earlier playback-only overlay artifact. It does not contain the custom core.
- `overlay/audio.bit` and its `.hwh` metadata: the audio-only milestone overlay — a project-built bitstream that drives the audio codec, without the accelerator core. Built by `overlay/build_audio.tcl`; loaded by `notebooks/w3_own_overlay_audio.ipynb` and `scripts/verify_own_overlay_audio.py`.
- `scripts/fir_core.py`: register-level driver for the core (AXI-Lite control, AXI4-Master data movement).
- `scripts/fir_audio_loop.py`: microphone → core → headphone loop, one block at a time. This is still the block-based path, not the real-time one.
- `scripts/fir_live.py`: microphone → core → headphone **real-time** path (record and play at once). Verified over continuous 8/10/24-second runs, all at 1.00× with no dropped block; the 24-second run moved 2,401 blocks and passed a 960,000/960,000-sample bit-exact self-check. The core interface was not changed and `fir.bit` was not rebuilt. **Update (2026-10-15): live monitoring works.** The 2026-10-13 "captured input driven into full-scale self-oscillation" was a misdiagnosis — a **reading artifact, not hardware**. The low 24 bits of the I2S register are two's complement and were read without sign extension; the DC offset (≈ 31,679 ≈ 2²³ × 0.966) sits right at the sign boundary, so the digital zero read as **rms 46,341 = 65535/√2**. The real blocker was **U41** on the `HP + Mic` jack (TI TS3A226AE auto-headset switch, which detects CTIA/OMTP once on the insertion edge and latches): **replug until the level meter jumps — 18 s of replugging, measured — then start and leave the plug alone.** A 40 s run (4,000 blocks, 40.00 s audio / 40.19 s wall = 1.00×, bit-exact self-check 960,000/960,000) put in-core rms median 107 / peak 24,076, and the user heard their own processed voice. That run had **all four band gains at 0.0 dB** (`LIVE_GAIN_DB` default), so it demonstrates the *path*, not the four-band effect — the four-band effect was measured separately the same day (see the evidence-boundary note below). It also overran by 0.19 s ≈ **18 dropped blocks** (audible stutter — the sample-mover thread spins at 99% of its 10 ms budget). The 2026-10-13 probe logs are kept as an example of the error, not as evidence.
- `scripts/audio_stream.cpp` and `scripts/build_audio_stream.sh`: a self-written streaming audio path, compiled on the board with gcc.
- `scripts/audio_stream_test.py`, `scripts/audio_overhead_probe.py`, `scripts/audio_duplex_probe.py`: tests and probes for that streaming path.
- `scripts/run_live.sh`: one command to start the real-time path.
- `scripts/fir_chunk_sweep.py`: block-size sweep for the core.
- `scripts/fir_selftest.py`: bypass and block-continuity checks.
- `scripts/compare_cpu_fpga.py`: same-board ARM baseline against the core.
- `scripts/mic_level.py`: prints the live input level in the int16 domain and flags anything below the silence threshold. `SILENT_RMS = 200` (full scale 32767) — the threshold is 200, not 8000; the 8000 value was a unit error fixed on 2026-10-02.
- `scripts/mic_live.py`: continuous input-level monitor with the same 200 threshold, printing peak alongside rms so a signal that is present but quiet is not mistaken for silence.
- `scripts/fir_tune.py`: reads and writes the two `/dev/shm` files the tuning notebook and `fir_live.py` exchange.
- `notebooks/w5_tuning_ui.ipynb`: the four-slider fitting interface. It only writes `/dev/shm/fir_tune.json` and reads `/dev/shm/fir_status.json`, so it can be closed or restarted without interrupting the audio stream.
- `notebooks/w3_own_overlay_audio.ipynb`: loads `overlay/audio.bit`, the audio-only milestone build that has no accelerator core, and confirms that a project-built bitstream can drive the audio codec.
- `notebooks/w2_audio_playback.ipynb`: base-overlay audio playback milestone notebook.
- `notebooks/test_multiband.ipynb`: board-side smoke test for the integrated `fir.bit` overlay; it uses the current `fir_core.py` register driver and verifies bypass delay and compression on a synthetic signal.
- `scripts/verify_audio_playback.py`: base-overlay audio playback and capture check.
- `scripts/test_overlay_load.py`: overlay load and IP inventory check.
- `scripts/verify_fir_hw_e2e.py`: DMA FIR test procedure for an overlay exposing the expected FIR DMA. Its default `fir_accel2.bit` and `filter.fir_dma` are not included in this repository.

## Keeping the board in sync

The board runs copies under `/home/xilinx/jupyter_notebooks/`, so a repository edit does nothing until the file is copied across. Two rules follow from that:

- **If `scripts/audio_stream.cpp` changes, `libaudio_stream.so` must be rebuilt.** Run `sh /home/xilinx/jupyter_notebooks/build_audio_stream.sh` **as the `xilinx` user, not with sudo**. On 2026-10-13 this was missed, and both the probe results and the `fir_live.py` fix silently measured the old library.
- **`scripts/mic_level.py` and `scripts/mic_live.py` must be re-copied whenever they change.** The repository uses `SILENT_RMS = 200`; the 8000 value they carried before 2026-10-02 is a unit error and would call a live microphone silent. Both board copies were verified byte-identical to the repository on 2026-10-15, so **no re-copy is outstanding**.

`overlay/fir.bit`, `overlay/audio.bit`, `scripts/fir_live.py`, `scripts/mic_level.py`, `scripts/mic_live.py` and `libaudio_stream.so` on the board are current as of 2026-10-15 (md5-checked, not assumed). The `audio_stream.cpp` changes dated 2026-10-13 touched comments only, so the existing `.so` is still correct.

## Evidence boundary

The custom-core board results are `data/results/fir_audio_loop_run_20261001.txt` and `data/results/compare_cpu_fpga_run_20261008.txt`; the write-up is `data/results/accel_cpu_vs_fpga.md`. The recorded 144,000-sample, 18-block capture passed the bypass identity check with zero mismatches, but its input level was nearly silent (effective value 1,342), so it does not prove an audible microphone A/B compression demo. A synthetic signal did demonstrate compression. A separate microphone → core → headphone real-time run (`scripts/fir_live.py`) ran 8/10/24 continuous seconds at 1.00× with no dropped block and passed a 960,000/960,000-sample bit-exact self-check; the core interface was not changed and `fir.bit` was not rebuilt. The 2026-10-15 run extends that to 40 s (4,000 blocks, 40.00 s of audio in 40.19 s of wall clock, bit-exact 960,000/960,000) and is the first run with the operator hearing their own processed voice; it used 0.0 dB on all four band gains, so it demonstrates the path rather than the four-band effect, and it overran by 0.19 s ≈ 18 dropped blocks because the audio IP has no DMA. The four-band effect was measured separately on the same day (`data/results/fir_gain_ab_run_20261015.txt`, reproduced by `scripts/gain_ab_live.sh`): one 66 s run in which the per-band gains were rewritten live through `/dev/shm/fir_tune.json` six times, alternating a single band between 0 dB and −41.9 dB. All six changes were applied, the bit-exact self-check still passed 960,000/960,000 with eight gain changes inside the run, the rate held at 1.00×, and the operator reported hearing several distinct changes. A prior attempt using a symmetric +6/0/0/−6 setting was inaudible — the gain did apply (input/output level ratio moved from 1.00 to 1.23) but it left total loudness nearly unchanged, so it is not quoted as evidence. The same-board ARM-vs-core comparison was measured on 2026-10-08: ARM 6.199 µs/sample (3.36× real time) against the core 0.317 µs/sample (65.69× real time), a 19.5× ratio. The matching comparison is ARM float64/scipy against PL Q1.15; it does not compare with a hand-written NEON CPU implementation.

`data/results/reference_overlay_metrics.md` records a different, earlier test: a separate open-source 27-tap FIR reference overlay. That result validates a board-side DMA path only and does not measure the custom core. Keep the two apart when quoting numbers.

Before running an overlay test, confirm the bitstream, `.hwh`, DMA/IP names, board image version, and expected sample format as a set. Do not substitute `ps_only.bit` into `verify_fir_hw_e2e.py`; it does not expose the FIR DMA expected by that script.
