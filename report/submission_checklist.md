# Submission Checklist

Status updated 2026-10-24 from the files currently present in this repository. A checked box means repository evidence exists; it does not replace team review or a live board demonstration.

## Repository, licence and naming

- [x] Top-level project directories and tracked source filenames use English names with lowercase letters, digits, underscore and hyphen only.
- [x] README describes the current repository status and the Python and HLS reproduction steps.
- [x] MIT license is present.
- [x] `.gitignore` excludes common generated files.
- [x] The three board bitstreams stay as plain tracked files. Each is 3.9 MB, well below GitHub's 100 MB per-file limit, so Git LFS would add a client dependency for no benefit.
- [ ] A clean-machine reproduction has not yet been performed. `BUILD_GUIDE.md` is written but has only been walked through on the development machine.

## Python, HLS and RTL

- [x] Current Python baseline and its plot/audio artifacts were regenerated on 2026-10-01.
- [x] The stored 1,000-sample HLS output was compared with regenerated Python golden data; SNR and transparent-mode alignment passed, and the comparison plot was generated.
- [x] C simulation rebuilt from source with Vitis HLS 2020.2 via `build/hls/run_csim.tcl`; the flow is now one command (`./rebuild.sh csim`) and prints `PASS` when the C simulation matches the integer reference model. The coefficient-width sweep outputs are in `data/results/fixed_dw16_cw12/16/18/20.txt`.
- [x] Python / HLS / RTL three-way comparison at one load: section 4.1 of the design report, `data/results/rtl_vs_hls.md`.
- [x] RTL implementation and simulation evidence: `src/rtl/fir_lp.v` and `src/rtl/fir_mb_fe.v` pass a bit-for-bit xsim comparison against the integer reference model on all nine observed signals; reproduce with `sim/rtl_sim/run_xsim.sh` and `run_xsim_mb.sh`.

## Hardware and board evidence

- [x] Custom HLS core out-of-context and integrated synthesis/implementation results are recorded in `data/results/impl_metrics.md`; the delivered build is `v25_env_indep` (36 cycles/sample, 205 of 220 DSP, integrated WNS +0.533 ns).
- [x] Reference overlay board measurements are documented separately in `data/results/reference_overlay_metrics.md` and are not presented as this project's accelerator result.
- [x] Custom HLS core integrated into the board overlay: `board/overlay/fir.bit`, built by `board/overlay/build_fir.tcl`; timing and utilisation in `board/overlay/fir_timing.rpt` and `fir_util.rpt`.
- [x] The bitstream in the repository and the bitstream on the board are the same file (md5 `5aa079f29e8d66c21d1d34f1302c3695`), checked before every board measurement from 2026-10-23 onward.
- [x] Custom-core board measurement: 144,000 samples of captured audio through the core in 18 blocks, bit-exact against the reference; bypass and block-continuity checks also passed.
- [x] Same-chip ARM-versus-core speedup, measured three times as the core changed: 19.5× against `v19` (31 cycles, 2026-10-08), 12.5× against `v21_wdrc` (49 cycles, 2026-10-22), and **16.7× against the delivered `v25_env_indep`** (36 cycles, 2026-10-23; ARM 6.117 µs/sample, core 0.367 µs/sample). Raw runs `compare_cpu_fpga_run_20261023.txt` and its dated predecessors; the caveats that must accompany the number are in `data/results/accel_cpu_vs_fpga.md`.
- [x] Live audio demonstration: microphone → core → headphone in real time. A 40 s run on 2026-10-15 (4,000 blocks, 40.00 s of audio in 40.19 s of wall clock, bit-exact self-check 960,000/960,000) was audible in the headphones. Two corrections are recorded because each was a false conclusion: the 2026-10-13 "full-scale self-oscillation" was a missing sign extension on the 24-bit capture word (the probe logs are kept as a record of the error and must not be cited as evidence), and the real blocker was U41, the auto-headset switch on the `HP + Mic` jack, which latches on the insertion edge — the demonstration begins with replugging until the input level meter jumps.
- [x] Per-band gain, live and audible: a 66 s run rewrote the four band gains six times while audio was flowing, one band alternating between 0.0 dB and −41.9 dB; all writes applied, the per-block self-check stayed bit-exact, the rate held at 1.00×, and the change was heard. Raw run `data/results/fir_gain_ab_run_20261015.txt`; reproduce with `board/scripts/gain_ab_live.sh`.
- [x] WDRC attack/release, live and audible: a 90 s run switched 0/0 → 5/100 ms → 0/0 with threshold and ratio fixed; both switches were heard. Raw run `data/results/fir_wdrc_ab_run_20261019.txt`; reproduce with `board/scripts/wdrc_ab_live.sh`.
- [x] Latency measured both ways: digital half from in-process timestamps, acoustic half from a sweep at the earpiece (R = 1.23 ms). Three configurations: 16.2 ms (L=360, NBUF=2), 21.1 ms (L=480, NBUF=2), 31.0 ms (L=480, NBUF=3). See section 5.5 and `data/results/latency_retest_20261023.md`.
- [x] Stability: two 600 s runs on 2026-10-23 at the demonstration configuration, both with the web UI and service running (L=480/NBUF=3: 60,192 blocks, **0 dropped**; L=360/NBUF=2: 80,103 blocks, **0 dropped**, maximum inter-block gap 3.85 ms), neither hung, self-check bit-exact on both. The 2026-10-24 recheck added a 600 s run at 16.2 ms (80,188 blocks, 0 dropped) and identified the probe instrumentation itself as the cause of the drops seen on the evening of 2026-10-23. Older 600 s runs that report dropped blocks are kept in `data/results/` as history and must not be cited as the current figure.

## Documentation and presentation

- [x] Collaboration logs are organized under `report/llm_collab_log/`.
- [x] Skill package contains prompts, templates, checkers, and pitfalls.
- [x] Target toolchain fixed and recorded: PYNQ-Z2 image 2.7.0 with Vivado and Vitis HLS 2020.2, with the reason for not using 2025.2/2026.1 stated in section 9 of the design report.
- [x] Interface specification: `report/hardware_interface_spec.md` for the register contract.
- [x] Final design report. `report/design_report_draft.md` carries the current numbers (36 cycles, 16.7×, +0.533 ns). The three sections the official guide asks for are now written: the functional block diagram and data path (§2.2, Figure 1), the hardware/software partitioning rationale with the register contract (§2.3), and the collaboration record plus skill-package distillation (§11, §12). Remaining work is a full read-through and any wording fixes it turns up.
- [x] Poster: out of scope for this submission. It is part of the finals (复赛) package and will be rebuilt then.
- [ ] Record and edit the final demonstration video, then add its link to the README.

## Final gate

- [ ] All final source and build files needed for reproduction are present, and a clean-machine run of `BUILD_GUIDE.md` has been recorded.
- [x] Board scripts are in sync with the repository: the overlay and driver scripts were re-copied and hash-checked on 2026-10-23, `mic_level.py` and `mic_live.py` use the 200 threshold, and the 2026-10-13 edit to `scripts/audio_stream.cpp` touched comments only, so `libaudio_stream.so` needs no rebuild.
- [ ] Every performance claim in the report, poster and video cites the measurement scope it came from (31 / 36 / 49 cycles, out-of-context against integrated).
- [ ] Submission package is checked against the official competition guide, tagged, and archived.
