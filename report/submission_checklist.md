# Submission Checklist

Status updated 2026-10-07 from the files currently present in this repository. A checked box means repository evidence exists; it does not replace team review or a live board demonstration.

## Repository and Python work

- [x] Top-level project directories and tracked source filenames use English names.
- [x] README describes the current repository status and Python reproduction steps.
- [x] MIT license is present.
- [x] `.gitignore` excludes common generated files.
- [x] Current Python baseline and its plot/audio artifacts were regenerated on 2026-10-01.
- [x] The stored 1,000-sample HLS output was compared with regenerated Python golden data; SNR and transparent-mode alignment passed, and the comparison plot was generated.
- [ ] A clean-machine reproduction has not yet been performed.
- [x] C simulation rebuilt from source on 2026-09-30 with Vitis HLS 2020.2 via `build/hls/run_csim.tcl`. The coefficient-width sweep outputs are in `data/results/fixed_dw16_cw12/16/18/20.txt`; the stored `hw_output.txt` and `hw_transparent_i16.txt` are the outputs the 2026-10-01 golden comparison was run against.
- [ ] Review whether the tracked board bitstreams should use Git LFS before final submission. `board/overlay/fir.bit` is the custom HLS design; `ps_only.bit` is an earlier playback-only artifact.

## Hardware and comparison evidence

- [x] Custom HLS core out-of-context synthesis and implementation results are recorded in `data/results/impl_metrics.md`.
- [x] Reference overlay board measurements are separately documented in `data/results/reference_overlay_metrics.md`.
- [x] Custom HLS core integrated into the board overlay: `board/overlay/fir.bit`, built by `board/overlay/build_fir.tcl`; timing and utilisation in `board/overlay/fir_timing.rpt` and `fir_util.rpt`.
- [x] Custom-core board measurement: 144,000 samples of captured audio through the core in 18 blocks, bit-exact against the reference; bypass and block-continuity checks also passed.
- [ ] Same-chip ARM-versus-core speedup: stale. `compare_cpu_fpga.py` was revised but has not been re-run on the board, so the previously recorded ARM 6.116 µs against the core's 4.507 µs per sample (1.4×) no longer applies and no current ratio is quoted. Caveats for any such number are recorded in `data/results/accel_cpu_vs_fpga.md`.
- [ ] Live audio demonstration clip: the real-time microphone → core → headphone path is working (`board/scripts/fir_live.py`; 8/10/24 s runs, all 1.00× real time, no block dropped, with the 24 s run self-checking 960,000/960,000 samples bit for bit), so the remaining step is only to record the clip. The earlier saved 2026-10-01 capture was nearly silent (effective value 1,342).
- [ ] Python/HLS/RTL comparison on the same input and with documented alignment and precision.
- [x] RTL implementation and RTL simulation evidence: `src/rtl/fir_lp.v` (three low-pass filters only — no subtractive band, DRC, or AXI shell) passes a bit-for-bit xsim comparison against the C simulation. The full three-way comparison is still outstanding.

## Documentation and presentation

- [x] Collaboration logs are organized under `report/llm_collab_log/`.
- [x] Added a dated review note to `2026-09-25_github_setup.md` covering the English-name rule and directory-structure cross-check without rewriting the historical prompt.
- [x] Skill package contains prompts, templates, checkers, and pitfalls.
- [x] Target toolchain fixed and recorded: PYNQ-Z2 image 2.7.0 with Vivado and Vitis HLS 2020.2. Every board measurement and synthesis record in this repository was produced with that pair; no other combination has been used.
- [ ] Final design report. `report/design_report_draft.md` now matches the integrated design; it still needs a final read-through.
- [x] Updated English poster exists at `report/poster_en/pynq_multiband_audio_poster_v2.pptx`; it includes the integrated-board status, current toolchain, and microphone-capture limitation. Team review is still required; `pynq_multiband_audio_poster_draft.pptx` is the superseded draft.
- [ ] Record and edit the final demonstration video, then add its link to the README.
- [x] Interface specification and toolchain version fixed: `report/hardware_interface_spec.md` for the register contract, PYNQ-Z2 image 2.7.0 with Vivado/Vitis HLS 2020.2 for the environment.

## Final gate

- [ ] All final source and build files needed for reproduction are present.
- [x] End-to-end audio path demonstrated on the target board: microphone → core → headphone. A block-at-a-time path exists in `board/scripts/fir_audio_loop.py`, and a real-time duplex stream is implemented in `board/scripts/fir_live.py` (8/10/24 s runs, all 1.00× real time, no block dropped), with no change to the core or its interface.
- [ ] All performance claims in the report and poster cite the correct measurement scope.
- [ ] Submission package is checked against the official competition guide.
