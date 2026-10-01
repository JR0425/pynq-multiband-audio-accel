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
- [ ] Vitis HLS C simulation has not been rebuilt in this environment; the documented Vitis HLS executable is not installed at that path.
- [ ] Review whether the tracked board bitstreams should use Git LFS before final submission. `board/overlay/fir.bit` is the custom HLS design; `ps_only.bit` is an earlier playback-only artifact.

## Hardware and comparison evidence

- [x] Custom HLS core out-of-context synthesis and implementation results are recorded in `data/results/impl_metrics.md`.
- [x] Reference overlay board measurements are separately documented in `data/results/reference_overlay_metrics.md`.
- [x] Custom HLS core integrated into the board overlay: `board/overlay/fir.bit`, built by `board/overlay/build_fir.tcl`; timing and utilisation in `board/overlay/fir_timing.rpt` and `fir_util.rpt`.
- [x] Custom-core board measurement: 144,000 samples of captured audio through the core in 18 blocks, bit-exact against the reference; bypass and block-continuity checks also passed.
- [x] Same-chip speedup measured: ARM 6.116 µs against the core's 4.507 µs per sample, 1.4×, with caveats recorded in `data/results/accel_cpu_vs_fpga.md`.
- [ ] Live audio demonstration clip: `board/scripts/fir_audio_loop.py` runs and writes the WAV files, but the microphone capture has not yet been verified at a usable level, so the audible A/B comparison is outstanding.
- [ ] Python/HLS/RTL comparison on the same input and with documented alignment and precision.
- [ ] RTL implementation and RTL simulation evidence.

## Documentation and presentation

- [x] Collaboration logs are organized under `report/llm_collab_log/`.
- [x] Added a dated review note to `2026-09-25_github_setup.md` covering the English-name rule and directory-structure cross-check without rewriting the historical prompt.
- [x] Skill package contains prompts, templates, checkers, and pitfalls.
- [ ] Review skill-package compatibility notes with the project team because the repo contains both PYNQ 2.7 / Vivado 2020.2 reference artifacts and a plan specifying PYNQ 3.1 / Vivado 2024.1.
- [ ] Final design report. `report/design_report_draft.md` now matches the integrated design; it still needs a final read-through.
- [x] English poster draft exists at `report/poster_en/pynq_multiband_audio_poster_draft.pptx`; team review is still required.
- [ ] Record and edit the final demonstration video, then add its link to the README.
- [ ] Confirm the final interface specification and toolchain version with the project team.

## Final gate

- [ ] All final source and build files needed for reproduction are present.
- [x] End-to-end audio path demonstrated on the target board: microphone → core → headphone, block-at-a-time, in `board/scripts/fir_audio_loop.py`. A sample-by-sample real-time stream is not implemented.
- [ ] All performance claims in the report and poster cite the correct measurement scope.
- [ ] Submission package is checked against the official competition guide.
