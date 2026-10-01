# Submission Checklist

Status updated 2026-10-01 from the files currently present in this repository. A checked box means repository evidence exists; it does not replace team review or a live board demonstration.

## Repository and Python work

- [x] Top-level project directories and tracked source filenames use English names.
- [x] README describes the current repository status and Python reproduction steps.
- [x] MIT license is present.
- [x] `.gitignore` excludes common generated files.
- [x] Current Python baseline and its plot/audio artifacts were regenerated on 2026-10-01.
- [x] The stored 1,000-sample HLS output was compared with regenerated Python golden data; SNR and transparent-mode alignment passed, and the comparison plot was generated.
- [ ] A clean-machine reproduction has not yet been performed.
- [ ] A fresh C-simulation rebuild from source has not been repeated in this execution environment. The stored HLS output/golden comparison is present and passed (the handover marks W3 complete); the teammate reports the Vitis HLS 2020.2 CLI worked at `E:\Xilinx\Vitis_HLS\2020.2\bin\vitis_hls.bat`, but that E: path is not mounted here, so an independent rebuild remains outstanding.
- [ ] Review whether the tracked board bitstreams should use Git LFS before final submission. `board/overlay/fir.bit` is the custom HLS design; `ps_only.bit` is an earlier playback-only artifact.

## Hardware and comparison evidence

- [x] Custom HLS core out-of-context synthesis and implementation results are recorded in `data/results/impl_metrics.md`.
- [x] Reference overlay board measurements are separately documented in `data/results/reference_overlay_metrics.md`.
- [x] Custom HLS core integrated into the board overlay: `board/overlay/fir.bit`, built by `board/overlay/build_fir.tcl`; timing and utilisation in `board/overlay/fir_timing.rpt` and `fir_util.rpt`.
- [x] Custom-core board measurement: 144,000 samples of captured audio through the core in 18 blocks, bit-exact against the reference; bypass and block-continuity checks also passed.
- [x] Same-chip speedup measured: ARM 6.116 µs against the core's 4.507 µs per sample, 1.4×, with caveats recorded in `data/results/accel_cpu_vs_fpga.md`.
- [ ] Live audio demonstration clip: the saved 2026-10-01 capture was nearly silent (effective value 1,342), so the audible microphone A/B is still outstanding. The script demonstrated compression with a synthetic signal; rerun with a usable mic or line input and record the clip.
- [ ] Python/HLS/RTL comparison on the same input and with documented alignment and precision.
- [ ] RTL implementation and RTL simulation evidence.

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
- [x] End-to-end audio path demonstrated on the target board: microphone → core → headphone, block-at-a-time, in `board/scripts/fir_audio_loop.py`. A sample-by-sample real-time stream is not implemented.
- [ ] All performance claims in the report and poster cite the correct measurement scope.
- [ ] Submission package is checked against the official competition guide.
