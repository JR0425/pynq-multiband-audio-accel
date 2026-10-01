# Submission Checklist

Status updated 2026-10-01 from the files currently present in this repository. A checked box means repository evidence exists; it does not replace team lead approval or a live board demonstration.

## Repository and Python work

- [x] Top-level project directories and tracked source filenames use English names.
- [x] README describes the current repository status and Python reproduction steps.
- [x] MIT license is present.
- [x] `.gitignore` excludes common generated files.
- [x] Current Python baseline and its plot/audio artifacts were regenerated on 2026-10-01.
- [x] The stored 1,000-sample HLS output was compared with regenerated Python golden data; SNR and transparent-mode alignment passed, and the comparison plot was generated.
- [ ] A clean-machine reproduction has not yet been performed.
- [ ] Vitis HLS C simulation has not been rebuilt in this environment; the documented Vitis HLS executable is not installed at that path.
- [ ] Review whether the tracked board bitstream should use Git LFS before final submission. The current `ps_only.bit` is a reference/playback artifact, not the custom HLS design.

## Hardware and comparison evidence

- [x] Custom HLS core out-of-context synthesis and implementation results are recorded in `data/results/impl_metrics.md`.
- [x] Reference overlay board measurements are separately documented in `data/results/reference_overlay_metrics.md`.
- [ ] Custom HLS core integration into the board overlay and end-to-end board measurement.
- [ ] Live audio demonstration of the custom core.
- [ ] Python/HLS/RTL comparison on the same input and with documented alignment and precision.
- [ ] RTL implementation and RTL simulation evidence.

## Documentation and presentation

- [x] Collaboration logs are organized under `report/llm_collab_log/`.
- [x] Added a dated review note to `2026-09-25_github_setup.md` covering the English-name rule and directory-structure cross-check without rewriting the historical prompt.
- [x] Skill package contains prompts, templates, checkers, and pitfalls.
- [ ] Review skill-package compatibility notes with the technical lead because the repo contains both PYNQ 2.7 / Vivado 2020.2 reference artifacts and a plan specifying PYNQ 3.1 / Vivado 2024.1.
- [ ] Final design report. The current file is still a draft and must be reviewed against the team's final hardware architecture.
- [x] English poster draft exists at `report/poster_en/pynq_multiband_audio_poster_draft.pptx`; team and technical lead review are still required.
- [ ] Record and edit the final demonstration video, then add its link to the README.
- [ ] Confirm the final interface specification and toolchain version with the technical lead.

## Final gate

- [ ] All final source and build files needed for reproduction are present.
- [ ] Final end-to-end audio path is demonstrated on the target board.
- [ ] All performance claims in the report and poster cite the correct measurement scope.
- [ ] Submission package is checked against the official competition guide.
