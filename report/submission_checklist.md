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
- [x] Same-chip ARM-versus-core speedup: measured on the board on 2026-10-08 after the core was reduced from 450 to 31 cycles per sample. ARM 6.199 µs/sample (3.36× real time) against the core 0.317 µs/sample (65.69× real time) — a ratio of 19.5×. Raw run in `data/results/compare_cpu_fpga_run_20261008.txt`; the caveats that must accompany the number are in `data/results/accel_cpu_vs_fpga.md`. **The bit on the board that day was `v19` (31 cycles). The delivered core, `v20_gain`, adds the per-band gain register and schedules at 35 cycles/sample (0.350 µs theory, ≈59× real-time margin); the ARM comparison has not been re-run against it, and scaling by cycle count puts it near 17× — a projection, not a measurement. Quote 35 for the shipping core and 31 only for the 19.5× run.**
- [ ] Live audio demonstration: the block-at-a-time microphone → core → playback path is demonstrated (`board/scripts/fir_audio_loop.py`), and the real-time duplex stream in `board/scripts/fir_live.py` runs at 1.00× (8/10/24 s; the 24 s run self-checked 960,000/960,000 samples bit for bit). **Live microphone monitoring works as of 2026-10-15.** A 40 s run (4,000 blocks, 40.00 s of audio in 40.19 s of wall clock, bit-exact self-check 960,000/960,000) put the in-core level at rms median 107 / peak 24,076, and the operator heard their own processed voice in the headphones. Raw output in `data/results/fir_live_run_20261015.txt`. Two corrections were needed, and both are recorded because each was a false conclusion:
  - **The 2026-10-13 "full-scale self-oscillation" was a reading artifact, not hardware.** The low 24 bits of the I2S capture register are two's complement and were read as unsigned; the capture path's DC offset (≈ 31,679 ≈ 2²³ × 0.966) sits at the sign boundary, so the digital zero read as rms 46,341 = 65535/√2. That is why the level appeared independent of microphone gain, muting, output level and headphone driver state — none of them was involved. The 2026-10-13 probe logs are kept as a record of the error and must not be cited as evidence.
  - **The real blocker was U41**, the auto-headset switch on the `HP + Mic` jack (TI TS3A226AE), which detects CTIA/OMTP once on the insertion edge and latches. The clip must begin with replugging until the input level meter jumps — 18 s of replugging, measured — and then leave the plug untouched.
  - Scope: the 40 s run held all four band gains at 0.0 dB (`LIVE_GAIN_DB` default), so by itself it demonstrates the path, not the audible four-band effect. **The four-band effect was demonstrated separately on the same day** in a 66 s run that rewrote the per-band gains live via `/dev/shm/fir_tune.json` six times, alternating one band between 0.0 dB and −41.9 dB: all six changes were applied, the bit-exact self-check still passed 960,000/960,000 with eight gain changes inside the run, the rate held at 1.00×, and the operator reported hearing several distinct changes. Raw output in `data/results/fir_gain_ab_run_20261015.txt`; reproduce with `board/scripts/gain_ab_live.sh`. A prior attempt with a symmetric +6/0/0/−6 setting was inaudible — the gain did apply (input/output level ratio 1.00 → 1.23) but total loudness barely moved — so it is recorded as a rejected method, not as evidence.
  - The 40 s run also overran by 0.19 s ≈ 18 dropped blocks (audible stutter; the sample-mover thread occupies 99% of its 10 ms budget).
  - The earlier saved 2026-10-01 capture was nearly silent (effective value 1,342), so the four `loop_*.wav` files remain the material for the block-based half of the demonstration.
- [ ] Python/HLS/RTL comparison on the same input and with documented alignment and precision.
- [x] RTL implementation and RTL simulation evidence: `src/rtl/fir_lp.v` (three low-pass filters only — no subtractive band, DRC, or AXI shell) passes a bit-for-bit xsim comparison against the C simulation. The full three-way comparison is still outstanding.

## Documentation and presentation

- [x] Collaboration logs are organized under `report/llm_collab_log/`.
- [x] Added a dated review note to `2026-09-25_github_setup.md` covering the English-name rule and directory-structure cross-check without rewriting the historical prompt.
- [x] Skill package contains prompts, templates, checkers, and pitfalls.
- [x] Target toolchain fixed and recorded: PYNQ-Z2 image 2.7.0 with Vivado and Vitis HLS 2020.2. Every board measurement and synthesis record in this repository was produced with that pair; no other combination has been used.
- [ ] Final design report. `report/design_report_draft.md` now matches the integrated design; it still needs a final read-through.
- [x] Updated English poster exists at `report/poster_en/pynq_multiband_audio_poster_v2.pptx`; it carries the integrated-board status, the current toolchain, and the 2026-10-15 live-run figures (1.00× real time over 40 s, bit-exact self-check) plus the 2026-10-15 live per-band-gain run. Team review is still required, and the file must be opened once in PowerPoint to confirm no text box overflows after the 2026-10-15 text edits, which were applied directly to the package XML. **Open question for the team: the poster's headline schedule figure is 449 cycles/sample and its resource chart is the matching out-of-context snapshot (`v13_sub_n193`), while the delivered core is 35 cycles/sample. The two are internally consistent and the notes slide discloses that they are core-only out-of-context results, but 449 is the pre-optimisation value and understates the delivered core by ~13×. Either regenerate the chart from the `v20_gain` flow (`build/hls/reports/v20_gain/` holds the csynth report; an out-of-context implementation run is needed for the resource percentages) and update the text, or keep the older snapshot and label it explicitly as a pre-optimisation baseline.** `pynq_multiband_audio_poster_draft.pptx` is the superseded 2026-10-01 draft — its body text still says live audio is pending, so do not use it.
- [ ] Record and edit the final demonstration video, then add its link to the README.
- [x] Interface specification and toolchain version fixed: `report/hardware_interface_spec.md` for the register contract, PYNQ-Z2 image 2.7.0 with Vivado/Vitis HLS 2020.2 for the environment.

## Final gate

- [ ] All final source and build files needed for reproduction are present.
- [x] End-to-end audio path demonstrated on the target board: microphone → core → headphone. A block-at-a-time path exists in `board/scripts/fir_audio_loop.py`, and a real-time duplex stream is implemented in `board/scripts/fir_live.py` (8/10/24 s runs, all 1.00× real time), with no change to the core or its interface. **Live monitoring is no longer blocked** — the 2026-10-13 input "self-oscillation" turned out to be a sign-extension reading artifact, and the real blocker was the U41 auto-headset switch on the `HP + Mic` jack; as of 2026-10-15 a 40 s live run carries audio end to end and the operator heard their own processed voice by ear. See the live-demonstration item above for the measured figures and the scope caveats (the 40 s run held all four band gains at 0.0 dB and dropped ~18 blocks; the audible four-band effect comes from the separate 66 s live-tuning run).
- [ ] All performance claims in the report and poster cite the correct measurement scope.
- [x] Board scripts are in sync with the repository before the final demonstration. The two gaps closed on 2026-10-15: the board copies of `scripts/mic_level.py` and `scripts/mic_live.py` were md5-checked and are already byte-identical to the repository (`SILENT_RMS = 200`), so no re-copy is needed; and the 2026-10-13 edit to `scripts/audio_stream.cpp` touched comments only, so `libaudio_stream.so` is still correct and does not need rebuilding. `overlay/fir.bit`, `overlay/audio.bit` and `scripts/fir_live.py` were also md5-checked the same day. Board and repository are in sync.
- [ ] Submission package is checked against the official competition guide.
