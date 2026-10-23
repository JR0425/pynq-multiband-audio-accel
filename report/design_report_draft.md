# PYNQ-Z2 Multiband Audio Processing Accelerator

## Design report draft

**Status as of 2026-10-15:** the Python reference path and the custom HLS core are present. The core is integrated into a board overlay together with the PYNQ audio codec (`board/overlay/fir.bit`), has processed real captured audio on the board, and now runs a real-time microphone → core → headphone duplex path (section 5.4); a 40 s live run on 2026-10-15 held 1.00× with a bit-exact self-check and was audible in the headphones (raw run in `data/results/fir_live_run_20261015.txt`). That run used the default flat per-band gains, so it demonstrates the path rather than per-band tuning; the per-band tuning itself was then demonstrated in a second 2026-10-15 run that changed the gains live while audio was flowing, with the operator confirming the change by ear (section 5.4). Out-of-context and integrated-in-overlay figures are reported separately and labelled; the reference-overlay measurements are a different experiment and are kept apart from the custom core. The same-chip ARM comparison was re-measured on the board on 2026-10-23 against the delivered 36-cycle core: 16.7× at 6.117 µs per sample for the ARM against 0.367 µs for the core (raw run in `data/results/compare_cpu_fpga_run_20261023.txt`). The earlier 12.5× figure belongs to the 49-cycle `v21_wdrc` bit and the 19.5× figure to the 31-cycle `v19` bit used for the three-way comparison in section 4.1. Section 7 states the novelty claim and the prior work it is measured against, including the one difference that runs against the project.

## 1. Project goal

The project explores a multiband dynamic-range-compression (DRC) audio pipeline on a PYNQ-Z2 board. DRC reduces the level of signals above a threshold. The algorithm divides audio into frequency bands, applies the same compression rule to each band, and sums the results.

The comparison uses a Python reference, an HLS implementation, and an RTL implementation. All three exist and are compared at one load in section 4.1: the Python reference, the HLS source, and a custom RTL front end (`src/rtl/fir_mb_fe.v`: three low-pass filters, the subtractive band split and the four-band sum — no DRC, gain or AXI shell), verified bit for bit in xsim.

## 2. Current algorithm

The current design uses a subtractive crossover. It designs three linear-phase low-pass filters at 500 Hz, 1,000 Hz, and 2,000 Hz, each with 193 taps. The bands are formed as follows:

```text
band 1 = LP500
band 2 = LP1000 - LP500
band 3 = LP2000 - LP1000
band 4 = delayed input - LP2000
```

The four bands sum to the input delayed by the FIR group delay before compression. At 193 taps, the group delay is 96 samples, or 2.00 ms at 48 kHz. The DRC uses a threshold of 0.1 and a slope of 0.7. The Python reference uses floating-point arithmetic; the HLS core uses a fixed-point input/output path. Quantization error must therefore be measured separately from algorithm alignment.

The single parameter source for sample rate, edges, and DRC defaults is `src/python/band_design.py`. HLS coefficients are generated into `sim/hls_csim/` by `src/python/export_coefficients.py`.

```mermaid
flowchart LR
    wav[44.1 kHz test WAV] --> prep[Mix channels, remove DC, resample to 48 kHz]
    prep --> py[Python reference<br/>193-tap FIR + four-band DRC]
    prep --> q15[Int16 / Q1.15 test input]
    q15 --> hls[HLS core<br/>three low-pass FIRs + subtractive bands + DRC]
    py --> pyout[Processed WAV and plots]
    hls --> simout[C-simulation output<br/>compared with Python golden]
    hls --> board[PYNQ audio overlay<br/>fir.bit: core + audio codec]
    ref[Open-source 27-tap reference overlay] --> refboard[Separate, earlier board-path evidence]
```

The diagram separates the project's custom HLS core from the open-source reference overlay. The custom core is integrated and has run on the board; the reference overlay is an unrelated earlier experiment.

## 3. Python reference implementation

`src/python/multiband_baseline.py` reads `data/audio/real_voice.wav`, mixes its stereo channels, removes the DC component, and resamples from the file's 44.1 kHz rate to the design rate of 48 kHz. It processes the signal with four subtractive bands and saves a WAV file and spectrum plot.

The current local run used Python 3.9.12, NumPy 2.0.2, SciPy 1.13.1, Matplotlib 3.9.4, and SoundFile 0.13.1. The latest best-of-five processing run took 89.5 ms for 10.58 s of audio, or 0.176 µs per sample and 118.3× host real time. The host CPU model was unavailable, so this is a local reference measurement, not a portable benchmark, and it must not be divided by the board-side figures — a 3 GHz desktop x86 and a 100 MHz FPGA differ by orders of magnitude by construction.

The comparison that carries the speedup claim is measured on the board itself, on the same chip, with the same algorithm on both sides: the Zynq ARM in software against the PL core. The latest run (2026-10-23, raw output in `data/results/compare_cpu_fpga_run_20261023.txt`) gives 6.117 µs per sample for the ARM (float64 + `scipy.signal.lfilter`, 3.41× real time) against 0.367 µs per sample for the delivered core (Q1.15 fixed point, AXI4-Master, 56.77× real time) — a ratio of 16.7×. The same measurement on the 49-cycle `v21_wdrc` bit gave 0.497 µs and 12.5×, and on the earlier 31-cycle `v19` bit 0.317 µs and 19.5×; the differences track the cycle count and the arithmetic the newer cores perform, not a change in methodology. `data/results/accel_cpu_vs_fpga.md` records the conditions and caveats that must travel with any such number: CPU float64 versus core Q1.15, and scipy's optimized C implementation (but no hand-written NEON comparison). Both sides compute the same number of effective multiplications (579 per sample); the earlier claim that the core halved its multiplication count via filter symmetry was wrong (`FIR_FOLD` is off). The PL time includes software register/cache handling — about 56 µs per 8000-sample block, under 10% of the measured per-sample time at this block size, and proportionally more for shorter blocks.

The current run reported a maximum coefficient-sum reconstruction error of `3.469e-18` before DRC. Its output artifacts are `data/audio/multiband_output.wav` and `data/figures/multiband_comparison.png`. The independent 1,000-sample test vector used by the HLS testbench is in `data/audio/test_input.txt`; the corresponding Python golden output is `data/results/python_golden.txt`.

## 4. HLS core results

`src/hls/fir_multiband.cpp` contains the subtractive FIR and DRC implementation. `src/hls/fir_tb.cpp` supplies the C-simulation testbench. Two implementations of the same 193-tap, fixed-point source are recorded, and their numbers must not be mixed:

| Metric | Core alone (out of context) | Core inside the board overlay |
| --- | ---: | ---: |
| LUT | 5,608 (10.54%) | 6,624 (12.45%) |
| Flip-flops | 8,884 (8.35%) | 10,281 (9.66%) |
| DSP | 205 (93.18%) | 205 (93.18%) |
| BRAM | 55 (39.29%) | 55 (39.29%) |
| Worst negative slack at a 10 ns constraint | +1.270 ns | +0.251 ns |
| Failing endpoints | 0 | 0 |
| HLS schedule | 49 cycles/sample | 49 cycles/sample |

Both columns now describe the delivered `v21_wdrc` build (2026-10-19). The out-of-context column is the core by itself, `build/hls/reports/v21_wdrc_impl/`; the integrated column is the full design as built into `board/overlay/fir.bit` on the same day. Use the integrated column when describing the system, and state which column a figure came from. The earlier v19 record (3,670 LUT / 200 DSP / +1.832 ns out of context, 6,127 LUT / 200 DSP / +1.283 ns integrated) is kept in `data/results/impl_metrics.md` for the 2026-10-08 ARM comparison and must not be mixed with these numbers.

The integrated slack fell from +1.283 ns (v19 overlay) to +0.251 ns with the envelope logic in place. It still meets the 10 ns constraint with zero failing endpoints, but the margin is now thin: any further PL addition must be re-measured, not assumed.

A note on the cycle count, because four values appear in the records and they belong to four different builds. The 2026-10-07 out-of-context run records 28 cycles/sample; re-running that same flow, and back-solving from the 2026-10-08 board measurement, both give 31. That 31 belongs to the bitstream measured on 2026-10-08, which did **not** yet contain the per-band gain. Adding `band_gain` on 2026-10-09 costs four more cycles — one multiply per band — and that build (`v20_gain`) schedules at **35 cycles/sample** (`build/hls/reports/v20_gain/`). Adding the peak envelope and attack/release smoothing on 2026-10-19 (`v21_wdrc`) costs another 14 cycles, so the delivered `board/overlay/fir.bit` is **49 cycles/sample**. The resource columns above are the v21 record; earlier builds had lower area and larger slack and are listed in `data/results/impl_metrics.md`. Quote **49** for the delivered core; 35 describes the 2026-10-09 to 2026-10-19 bit; 31 describes the bit that the ARM comparison in section 3 was measured on; 28 belongs only to the intermediate t-series experiments.

At 100 MHz, the 31 cycles behind the 2026-10-08 measurement correspond to a theoretical 0.310 µs per sample against the 20.83 µs sample period at 48 kHz, and the board measurement of that same bitstream is 0.317 µs per sample over 8000-sample blocks (65.69× real time). The 0.007 µs per sample gap is the per-block software overhead — register writes, cache maintenance and DONE polling — some 52–56 µs per 8000-sample block, about 2% of the measured time at this block size. The 49-cycle `v21_wdrc` bitstream was measured on 2026-10-22 at 0.497 µs per sample, and the delivered 36-cycle `v25_env_indep` bitstream on 2026-10-23 at 0.367 µs per sample — a 56.8× real-time margin and a 16.7× ratio against the ARM, so the projection that used to stand here (12–13× by cycle count) is no longer needed. The 19.5× ratio in section 3 describes the 31-cycle bit. Both figures sit far above the 30% improvement the rubric asks for. (An earlier version of this paragraph paired a 0.037 µs figure, computed against the superseded 28-cycle count, with a 52 µs figure computed against 31; the two did not belong together. See the 2026-10-15 note in `data/results/accel_cpu_vs_fpga.md`.)

The core's cost was dominated by data movement, not arithmetic: the synthesis loop table attributed 398 of the original 450 cycles to shifting the 193-entry delay line, with the three low-pass filters together taking 11 cycles. That movement is now removed by the compile-time switch `-DFIR_SHIFT_CHAIN=1`, which cuts the schedule from 450 to 31 cycles per sample in v19 — 49 in the delivered v21, after the per-band gain and the envelope were added — without changing the algorithm; the switch defaults off and the default build path is byte-for-byte unchanged. The change is implemented and verified on the board, not left as future work.

The implementation history and measurement limitations are documented in `data/results/impl_metrics.md`. Do not compare the out-of-context rows with rows measured using a different flow without accounting for the methodology change.

### 4.1 Three-way comparison at one load (Python / HLS / RTL)

All three rows compute the same front end: three 193-tap low-pass filters (allocated as 195 taps, six of them zero), pairwise subtraction into four bands, and the band sum. None of them contains a compressor, a per-band gain or an AXI shell. The ARM row performs 579 multiplications per sample; the two hardware rows perform 585 (579 effective, the remainder zero taps). The sample period at 48 kHz is 20.83 µs.

| | ARM Cortex-A9 (software) | HLS core, `v19_shift_chain` | RTL front end, `fir_mb_fe.v` |
| --- | ---: | ---: | ---: |
| Arithmetic | float64, `scipy.signal.lfilter` | Q1.15, 18-bit Q1.17 coefficients | Q1.15, same fixed-point contract |
| Cycles per sample | — | 31 | **3** (3,000 cycles over 1,000 samples, xsim) |
| Time per sample at 100 MHz | 6.199 µs (measured) | 0.310 µs theoretical / 0.317 µs measured | 0.030 µs (projection from simulated cycles) |
| Real-time headroom at 48 kHz | 3.36× | 65.7× (measured) | ≈694× (projection) |
| Speedup against the ARM | 1× | **19.5× (measured)** | ≈207× (projection) |
| LUT | n/a (uses 1 of 2 cores) | 3,670 (6.90%) | 4,559 (8.57%) |
| Flip-flops | n/a | 5,547 (5.21%) | 3,138 (2.95%) |
| DSP48 | n/a | 200 (90.91%) | 189 (85.91%) |
| BRAM | n/a | 49 (35.00%) | 0 |
| WNS at the 10 ns constraint, post-route | n/a | +1.832 ns | +0.585 ns |
| Failing endpoints | n/a | 0 | 0 |

The HLS column is `v19_shift_chain` because it is the only HLS build with the same function. The delivered `v21_wdrc` adds per-band gain and the peak envelope and schedules at 49 cycles per sample; putting the 3-cycle front end next to 49 cycles would overstate the RTL advantage, so it is not used here.

Both hardware columns are post-place-and-route, out of context, on the same device with the same 10 ns constraint. Against HLS, the RTL uses 889 more LUTs (+24%) and has 1.247 ns less slack, but 2,409 fewer flip-flops (−43%), 11 fewer DSPs and no BRAM. The cycle advantage comes from keeping all 195 multipliers busy every cycle with three coefficient phases per sample, where the HLS schedule feeds the same multipliers five partial sums at a time.

Only the ARM and the HLS core were measured on hardware. The RTL column is simulation plus out-of-context place-and-route: it has no AXI shell and has never run on the board, so its 0.030 µs excludes the PS-side cost that the HLS board measurement includes (register writes, cache maintenance and DONE polling, about 52–60 µs per 8,000-sample block, under 10% at that block size). The ≈207× ratio is therefore a projection, not a same-chip measurement; the measured same-chip figures are 19.5× for the 31-cycle front end in this table and **12.5× for the delivered 49-cycle core**.

The two fixed-point implementations agree with their references numerically: the RTL front end is bit-identical to the integer reference model on all nine observed signals — three accumulator taps, four bands, the band sum and the final int16 output, 1,000/1,000 samples each — and the HLS core's C simulation matches the Python golden reference of the full algorithm at 75.4 dB SNR with the best lag at zero (section 6). The ARM and the hardware are not bit-comparable because of precision (float64 against Q1.15); their agreement was checked at output RMS and peak (0.10659 against 0.10706, and 0.28627 against 0.28625).

Sources: `data/results/accel_cpu_vs_fpga.md`, `impl_metrics.md`, `rtl_vs_hls.md`; raw logs `compare_cpu_fpga_run_20261008.txt`, `sim/rtl_sim/xsim_work_mb/xsim_mb.log`, and reports `build/hls/reports/win_shiftchain_impl/` and `data/results/rtl_fe_impl_{util,timing}.rpt`.

## 5. Board evidence

The project overlay is `board/overlay/fir.bit`: the custom multiband HLS core together with the PYNQ audio codec, built from `board/overlay/build_fir.tcl`. `board/scripts/fir_core.py` drives the core over AXI-Lite, and `board/scripts/fir_audio_loop.py` runs microphone → core → headphone one block at a time. A real-time duplex path (`board/scripts/fir_live.py`, with its own streaming audio path in `board/scripts/audio_stream.cpp` and `build_audio_stream.sh`) now runs microphone → core → headphone continuously, without any change to the core. The duplex data path is verified bit for bit, and since 2026-10-15 live monitoring works end to end — see section 5.4.

The recorded on-board checks passed:

| Check | Result |
| --- | --- |
| Bypass: output equals the input delayed by the group delay | 0 mismatches over 1,000 samples; delay exactly 96 samples |
| Block continuity: 157-point blocks against a single block | bit-identical |
| Real captured audio through the core: 144,000 samples in 18 blocks | 0 mismatches |
| Quantisation SNR against the Python golden reference | 75.4 dB (criterion ≥ 70 dB) |
| Bypass over the fixed input: 288,000 samples in 36 blocks | 0 mismatches |
| Compressed output against bypass, same input | differs at 287,910 of 288,000 samples (100.0%) |

### 5.1 Fixed input and the idempotence check

The demonstration input is now a fixed file, `data/audio/board_input_48k.wav`, built by `src/python/export_board_input.py` from `data/audio/real_voice.wav`. It is conditioned exactly as the Python reference conditions its own input — channel sum, DC removal, `resample_poly` to 48 kHz — and the resampled length is 507,905 samples, matching `data/results/baseline_metrics.md`. A fixed file makes parameter comparisons meaningful: every run consumes the same 288,000 samples, so a difference between two parameter sets can only come from the parameters.

The core's input gain is computed by one formula that appears in both the generator and the board script (one in the float domain, one in the int16 domain, algebraically equivalent). Because the file is already at the target level, the board reports a gain of exactly **1.0000×**. That makes the report a check rather than a number: any other value means the wrong file was loaded or the two formulas have diverged.

| Property of the fixed input | Value |
| --- | --- |
| Length | 288,000 samples = 6.00 s at 48 kHz |
| RMS | 4,915 (15.0% FS) |
| Peak | 27,346 (83.5% FS) |
| Crest factor | 5.56 |
| Gain the board computes for it | 1.0000× |

### 5.2 Compressor transfer curve

The compressor is characterised the standard way, with a level staircase: six steps of 1 s, 8 dB apart, each step a sum of four tones (250, 700, 1400, 4000 Hz) at fixed relative phase. The staircase runs 40 dB down, deeper than speech, so it shows the shape of the whole curve rather than only the part speech occupies. After matching the loudest step for equal loudness:

| Step | Input | Bypass | Compressed | Lift over bypass |
| --- | --- | --- | --- | --- |
| 0 dB | 6,554 | 6,554 | 6,554 | 0.0 dB |
| −8 dB | 2,609 | 2,609 | 3,323 | +2.1 dB |
| −16 dB | 1,039 | 1,039 | 2,000 | +5.7 dB |
| −24 dB | 414 | 414 | 1,131 | +8.7 dB |
| −32 dB | 165 | 165 | 450 | +8.7 dB |
| −40 dB | 66 | 66 | 179 | +8.7 dB |

**Input span 40.0 dB, output span 31.3 dB — the level span is narrowed by 8.7 dB.** The plateau over the bottom three steps is the expected shape of a hard-knee compressor: below the threshold the curve is the identity, so those steps are lifted by the same constant.

The compressor is **instantaneous**: it maps each sample through a per-band piecewise-linear curve with no attack or release time constant. This matters for how the parameter sweep should be read. The whole-signal crest factor barely moves across the sweep — 5.02 to 5.34, a 0.3 dB spread — while the frame-level dynamic range moves a great deal:

| Parameter set | Threshold / ratio | Frame dynamic range (20 ms, 95th/5th pct) | Narrowed | Crest factor |
| --- | --- | --- | --- | --- |
| Bypass | — | 22.1 dB | — | 5.56 |
| 1 | 0.100 / 0.70 | 20.3 dB | 1.9 dB | 5.04 |
| 2 | 0.050 / 0.70 | 19.4 dB | 2.7 dB | 5.26 |
| 3 | 0.030 / 0.70 | 19.2 dB | 3.0 dB | 5.34 |
| 4 | 0.030 / 0.50 | 17.1 dB | 5.0 dB | 5.07 |
| 5 | 0.020 / 0.40 | 16.0 dB | 6.2 dB | 5.02 |
| 6 | 0.010 / 0.30 | 15.6 dB | **6.5 dB** | 5.08 |

The board's own sweep report prints the crest factor, and that is the wrong column to select on: it spans 0.3 dB across the six candidates, so it cannot discriminate between them. Selection should use the frame-level dynamic range, which spans 4.6 dB. Both figures are measured, not simulated — the offline replica used for the table reproduces the board's own 4c sweep to 0.1 dB.

Two numbers describe the compression and both belong in any summary. On the staircase the span narrows by 8.7 dB; on the actual speech material it narrows by 6.5 dB. The staircase figure is larger because the staircase extends 40 dB down. Quoting only the larger one would overstate the effect on the delivered material.

### 5.3 Spectral effect

Compression is applied per band before the bands are summed, and the four bands are not compressed equally, so the spectral balance shifts as well as the dynamics. Measured on the fixed input:

| Band | Bypass RMS | Compressed RMS | Compressor gain | Net change after loudness matching |
| --- | --- | --- | --- | --- |
| 0–500 Hz | 0.1009 | 0.0352 | −9.15 dB | −0.41 dB |
| 500–1000 Hz | 0.0563 | 0.0215 | −8.36 dB | +0.38 dB |
| 1000–2000 Hz | 0.0228 | 0.0107 | −6.60 dB | +2.14 dB |
| 2000–24000 Hz | 0.0311 | 0.0136 | −7.22 dB | +1.51 dB |
| *(parameter set 6: threshold 0.010, ratio 0.30)* | | | | |

One measurement caveat belongs with this table. The bands are derived by subtraction, so their transition regions overlap and cancel; the four band signals sum back to the input (to `4.4e-16`, once the 96-sample group delay is accounted for), but their RMS values do not add in power. "Share of total energy" is therefore not a meaningful quantity for this filter bank, and the table gives per-band gains and net changes instead.

The shape is the same for every parameter set, with the spread scaling with the setting: the 0–500 Hz band is compressed hardest and the 1–2 kHz band least, a 2.55 dB spread at set 6 and 2.04 dB at set 4. After equal-loudness matching the low band returns essentially to where it started (−0.41 dB) while the mid band is lifted (+2.14 dB). The net effect is a small upward tilt — about 1.9 dB for set 6 and 1.7 dB for set 4, measured as the 2–24 kHz band against the 0–500 Hz band.

Two consequences are worth stating. First, a per-band instantaneous compressor changes timbre at least as readably as it changes dynamics, so the audible result is a modest dynamic-range reduction plus a small spectral tilt rather than a dramatic loudness effect. Second, a loudness-preserving compressor that leaves the material sounding like the same speaker is behaving correctly; "it sounds similar" is the expected result, not a failure, and the quantitative characterisation above is what demonstrates the effect.

### 5.4 Demonstration path

The demonstration now runs as a real-time duplex stream: `board/scripts/fir_live.py` drives microphone → core → headphone continuously for 8, 10, and 24 s, all at 1.00× real time with no block dropped (the 24 s run self-checked 960,000 of 960,000 samples bit for bit). This was achieved without changing the core or its interface — `fir.bit` was not rebuilt, and no streaming interface was added. The core's throughput was never the obstacle; the bottleneck is on the Python/PS side: the audio driver's `play()` spends 3.33 ms per call on eight I2C transactions, the audio IP has no DMA so streaming in and out each occupy a core, and the format conversion runs on the sample-feeding thread, which has zero margin.

The same-board speedup comparison, re-measured on 2026-10-08, is discussed there (section 3). `data/results/reference_overlay_metrics.md` records a different, earlier experiment: an open-source 27-tap reference overlay. It validates a board-side DMA path but does not execute the custom core and must not be presented as this project's accelerator result.

**Live monitoring (resolved 2026-10-15).** Live microphone monitoring now works: a 40 s run drove microphone → core → headphone continuously (4,000 blocks, 40.00 s of audio in 40.19 s of wall clock = 1.00× real time, bit-exact self-check 960,000/960,000), with an in-core level of rms median 107 and peak 24,076, and the operator heard their own processed voice in the headphones. Two things had to be corrected to get there, and both are worth recording because each was a false conclusion.

*The "self-oscillation" was a reading artifact, not hardware.* The 2026-10-13 finding that the captured input is "driven into full-scale self-oscillation within about 0.7 s" does not describe anything in the hardware. The low 24 bits of the I2S capture register are two's complement, and the capture scripts were reading them as unsigned: any negative sample then becomes a value near full scale. The capture path carries a DC offset of roughly 31,679 in the 16-bit domain — 2²³ × 0.966 in the 24-bit domain, sitting right at the sign boundary — so the digital zero, with its few LSBs of jitter, was read as a near-full-scale square wave, giving an rms of exactly 46,341 = 65535/√2. That is why the level appeared independent of microphone gain, microphone muting, output level and the headphone driver state: none of those was ever involved. After sign-extending (mask the low 24 bits, subtract 2²⁴ when the sign bit is set, divide by 256), the readings are correct. **A full-scale reading that lands exactly on a clean theoretical number is a hint to check the read path, not the hardware.**

*The actual blocker was the headset jack's auto-detect switch.* The `HP + Mic` jack contains U41, a TI TS3A226AE automatic headset switch that detects CTIA/OMTP wiring **once on the insertion rising edge and latches**. If the board is powered up with the plug already inserted, that edge never occurs and the switch can sit in the wrong state, leaving the microphone disconnected. The remedy is to replug until the input level meter jumps — measured at 18 s of continuous replugging in one trial and 35.5 s in another — and then to leave the plug alone, since any further movement, or a power cycle, can latch it wrong again. The demonstration therefore begins with that replug step. A 90 s single-action probe (`board/scripts/cap_probe16.py`) captured the transition directly: the first 17 s sat at rms 13–17, the 18th second peaked at 45,104, and the level stayed live afterwards, dropping back to rms ≈ 12 only during the intervals when the plug was out.

*Scope of this result.* All four band gains were at 0.0 dB in that 40 s run (the `LIVE_GAIN_DB` default), so by itself it demonstrates that the real-time path carries audio end to end. The audible effect of the four-band controls was measured separately, in the same session, with a 66 s run that rewrote the per-band gains six times while audio was flowing: one band alternated between 0.0 dB and −41.9 dB (the Q7.8 floor, `GAIN_LIMIT_DB` in `fir_live.py`) at 12 s intervals, an earlier symmetric +6/0/0/−6 setting having proved inaudible. The board reported all six changes applied, the per-block self-check still passed 960,000/960,000 with eight gain changes inside the run, the rate held at 1.00×, and the operator reported hearing several distinct changes. As an in-run comparison there is no dependence on memory or on reloading the overlay between trials. Raw output in `data/results/fir_gain_ab_run_20261015.txt`; reproduce with `board/scripts/gain_ab_live.sh`. Finally, the 40 s run overran its 40.00 s of audio by 0.19 s, i.e. about 18 dropped blocks, heard as intermittent stutter: the sample-mover thread occupies 99% of its 10 ms per-block budget (median 9.88 ms, max 15.5 ms). That is a structural limit of the current audio IP, which has no DMA and is driven by spin-polling from the CPU — not a parameter that can be tuned away.

### 5.5 End-to-end latency (measured 2026-10-22)

Two measurements, one for each half of the path. Full data and method are in
`data/results/performance_profile_20261022.md`.

**Digital half.** Instead of a click stimulus, `fir_live.py` timestamps every block at
the two ends of `duplex_block()`: the instant the block's samples were captured, and the
instant the same block was handed to the codec. Both timestamps are taken at the start of
the same call, so the in-block offset cancels and the difference is the digital latency of
the pipeline. Median over four configurations:

| Config | Median digital latency |
| --- | --- |
| NBUF=3, L=480 (stable) | 29.80 ms |
| NBUF=2, L=480 | 19.86 ms |
| NBUF=2, L=360 (low-latency) | 14.96 ms |
| NBUF=2, L=240 | 9.99 ms |
| NBUF=2, L=120 | 5.78 ms |

The last two do not hold real time (0.83× and 0.42× over 25–30 s; 1,053 and 5,843 dropped
blocks), so they are measurements of what the buffering would cost, not usable
configurations. The 29.80 ms figure is what `NBUF × L / 48000` predicts (30 ms), i.e. the
latency is buffering, not compute: the core consumes 2.35% of the block period.

*Repeatability of the 19.86 ms figure.* The NBUF=2 configuration was re-run for 120 s with
the same instrumentation: 12,071 blocks, 120.71 s of audio in 120.24 s of wall clock
(1.00×), median 19.89 ms, p99 20.56 ms, maximum 28.92 ms, 8 blocks (0.07%) above 25 ms,
per-block self-check 960,000/960,000. The distribution matches the 20 s run, so the figure
is not one lucky capture. Those eight outliers are blocks where the sample-mover thread
overran its 10 ms budget (12.62 ms maximum in the same run); they delay a block rather than
dropping it. No 600 s run exists for this configuration; its stability evidence is 120 s.

*Where the block-length floor actually is.* A sweep over seven block lengths (25–30 s each)
moved the usable floor from 10 ms to 7.5 ms: L=360 holds 1.00× over 120 s with median
14.96 ms, p99 15.30 ms and no block above 25 ms, whereas L=300 (6.25 ms) already loses 1.6%
of its blocks and L=264 (5.5 ms) loses 8%. Instrumenting the four instants inside the
sample-mover loop rules out the software side: waiting for a processed buffer takes 23 µs,
queueing 19 µs, the DSP thread uses 18–25% of the block period, and raising the sample
mover's `SCHED_FIFO` priority above that of every kernel thread (20 → 60, with eight
`FIFO 50` kernel workers on the same core) changed nothing (5.92 vs 5.93 ms). The failing
part is the per-sample I2S handshake inside `duplex_block`, which cannot sustain the 48 kHz
cadence once the block falls below about 6 ms. Lower latency therefore needs a change to
the audio IP — a deeper FIFO or DMA — not to the Python side.

**Acoustic half.** `board/scripts/acoustic_lat.py` holds the earpiece against the on-board
microphone, plays a 500–6000 Hz linear sweep six times per amplitude setting, averages the
six captures synchronously (coherent echo adds, random room noise falls as √6) and
cross-correlates the average against the sweep template. All three amplitude settings
return the peak at the same sample: **R = 1.23 ms**, which is the DAC reconstruction,
headphone, air, microphone and ADC anti-alias path combined. The 1.23 ms figure was checked
against the sweep's own autocorrelation sidelobes (±0.29 ms, 32%); there is no independent
peak earlier than 1 ms (0.15 ms holds 2% of the main peak), so it is not electrical
crosstalk.

| Configuration | Digital | + analog/air | Mouth to ear |
| --- | --- | --- | --- |
| L=480, NBUF=3 (stable) | 29.80 ms | 1.23 ms | **31.0 ms** |
| L=480, NBUF=2 | 19.86 ms | 1.23 ms | **21.1 ms** |
| L=360, NBUF=2 (low-latency) | 14.96 ms | 1.23 ms | **16.2 ms** |
Scope: the low-latency configuration reaches 16.2 ms and the stable configuration 31.0 ms.
Both are well below Bluetooth audio (100–200 ms) but above the ≤10 ms target for hearing
aids, and the report should say so rather than claim hearing-aid-grade latency. The
remaining gap to 10 ms is the per-sample I2S handshake, which limits the block to about
6 ms; closing it requires a deeper FIFO or DMA in the audio IP, not a software change.

Two measurement errors were found and fixed while taking these numbers, both recorded
because they would mislead anyone repeating the work: the two new scripts initially called
`configure()` without `select_microphone()`, so the ADC sampled the unconnected line input
and read a constant 0.7 LSB; and the first run, made while the web UI service was also
running, left the board unresponsive and required a power cycle.

## 6. Validation status

| Check | Status at this snapshot |
| --- | --- |
| Python subtractive-band reconstruction identity | Regenerated locally; maximum error `3.469e-18` |
| Python audio output and spectrum | Regenerated locally on 2026-10-01 |
| Python time-frequency waterfall | Generated locally on 2026-10-01 |
| Python golden output for the current 1,000-sample test vector | Regenerated locally on 2026-10-01 |
| Stored HLS output compared with the regenerated golden file | Passed on 2026-10-01: 75.4 dB SNR, best lag 0; the stored transparent output matched bit for bit at 96 samples |
| Rebuilding the HLS C simulation | Rebuilt from source on 2026-09-30 with Vitis HLS 2020.2 via `build/hls/run_csim.tcl`; the coefficient-width sweep outputs are kept in `data/results/fixed_dw16_cw12/16/18/20.txt` |
| Custom HLS core out-of-context implementation | Results recorded in `data/results/impl_metrics.md` |
| Custom HLS core integrated into the board overlay | Complete: `board/overlay/fir.bit`; timing and utilisation in `board/overlay/fir_timing.rpt` and `board/overlay/fir_util.rpt` |
| Custom-core board audio through the real capture path | Complete: 144,000 samples in 18 blocks, 0 mismatches |
| Custom-core board audio through the fixed input | Complete on 2026-10-06: bypass 0 mismatches over 288,000 samples; compressed output differs at 287,910 of 288,000 |
| Compressor input-output level curve | Measured on 2026-10-06: 40.0 dB in against 31.3 dB out, narrowed 8.7 dB; figure in `data/figures/drc_transfer_curve.png`, raw run in `data/results/fir_audio_loop_run_20261006.txt` |
| Same-chip ARM against PL core timing | Measured on 2026-10-08 against the 31-cycle `v19` bit: ARM 6.199 µs/sample (3.36× real time) against the PL core 0.317 µs/sample (65.69× real time), a 19.5× ratio; raw run in `data/results/compare_cpu_fpga_run_20261008.txt`, conditions and caveats in `data/results/accel_cpu_vs_fpga.md`. The delivered 49-cycle `v21_wdrc` bit has not been re-measured against the ARM; scaling by cycle count puts it near 12–13×, which is a projection. |
| Real-time microphone → core → headphone duplex path | Complete on 2026-10-15: 40 s at 1.00× (4,000 blocks, 40.00 s of audio in 40.19 s of wall clock) with a bit-exact self-check of 960,000/960,000; the operator heard their own processed voice. Raw run in `data/results/fir_live_run_20261015.txt`. All four band gains were at 0.0 dB, so this establishes the path, not the per-band effect. |
| Per-band gain, runtime writes | Complete on 2026-10-15: six live writes to the four `band_gain` registers inside one continuous 66 s run, all applied, bit-exact self-check still 960,000/960,000 across eight gain changes, rate held at 1.00×. Raw run in `data/results/fir_gain_ab_run_20261015.txt`; reproduce with `board/scripts/gain_ab_live.sh`. |
| Per-band gain, audible effect | Complete on 2026-10-15: the same 66 s run alternated one band between 0.0 dB and −41.9 dB (the Q7.8 floor), and the operator reported several distinct audible changes. A symmetric +6/0/0/−6 setting was tried first and was inaudible — the gain applied (level ratio 1.00 → 1.23) but total loudness barely moved — so the demonstration uses a large single-band change. |
| RTL implementation and Python/HLS/RTL comparison | Complete. RTL front end `src/rtl/fir_mb_fe.v` (three low-pass filters, subtractive band split, four-band sum; no DRC, gain or AXI shell) verified bit for bit in xsim on nine signals × 1,000 samples at 3,000 cycles per 1,000. Three-way table in section 4.1: 3 cycles/sample against 31 for the same-function HLS build; 4,559 LUT / 3,138 FF / 189 DSP / 0 BRAM, +0.585 ns post-route. The RTL has not run on the board. |

The current stored HLS output passes comparison against the newly generated 193-tap Python golden file — 75.4 dB SNR at best lag 0 — and the stored transparent output matches the input delayed by the expected 96 samples bit for bit. The C simulation behind those outputs was rebuilt from source on 2026-09-30 with Vitis HLS 2020.2, so the producing toolchain and flags are on record rather than inferred from filenames: `build/hls/run_csim.tcl` drives the build and the coefficient-width sweep outputs are kept in `data/results/fixed_dw16_cw12/16/18/20.txt`.

## 7. Related work and novelty

This section answers the comparison the rubric asks for — what predecessors have done, and where this project differs — including one difference that runs against us.

### 7.1 What predecessors have done

| Project | Platform | What it covers | Runtime per-band gain / fitting UI |
|---|---|---|---|
| openMHA | PC / ARM | Complete hearing-aid chain: multiband dynamic compression, beamforming, noise reduction, NAL-NL2 fitting | Fitting exists, but on the CPU — no FPGA involved |
| Tympan / CHAPRO GHA | Teensy (MCU) | 8-band FIR filterbank with full WDRC (threshold, knee, ratio, attack/release) | Yes — a phone controls the MCU over BLE |
| `dennisongit/fpga-hearing-aid-sim` | RTL simulation only | 5-band Linkwitz-Riley with per-band WDRC and limiting | No — parameters are module ports, fixed at synthesis |
| `mastersim7/vhdl-hearing-aid` | ASIC (0.13 µm) | Filterbank plus gain stage | No |
| `VRM21-Studios` audio module series | RTL | Gain, envelope and FIR modules behind AXI-Lite | Yes, but single-band and generic |
| 18-band IFIR with log-domain DRC (paper) | Zynq-7000 prototype board | 1/3-octave filterbank with log-domain compression | No — parameters fixed |
| **PYNQ/Zynq + runtime fitting** | — | — | **The search came back empty; no precedent found** |

Read as one line: the algorithm side of hearing aids is crowded — thresholds, compression ratios and fitting curves are published everywhere. What the search did not find is **runtime fitting implemented in the programmable logic and driven from a CPU-side control plane**. That cell is empty, and the emptiness is checkable: the same search terms return the same result for anyone who repeats them.

Three qualifications travel with this table.

1. **"No precedent" is not "we were first."** The empty cell is *PYNQ/Zynq plus runtime fitting*, not hearing aids, and not FPGAs. The claim made here is only that this particular cell is empty, and that claim is verifiable. No claim is made that this project pioneered FPGA hearing aids.
2. **The two closest RTL projects are zero-star student projects.** The `dennisongit/...` repository contradicts itself in its own documentation — 5 bands in one place and 8 in another, Q1.17 against Q2.30 — which reads as generated text rather than measured work. Its *structure* is cited here; none of its numbers are. The numbers that are cited come from openMHA and Tympan/CHAPRO on the software side and from the 18-band IFIR paper on the hardware side.
3. **The algorithm is deliberately not reinvented.** Threshold, compression ratio and time-constant semantics follow openMHA and Tympan rather than being invented here. Those parameters are a solved problem, and re-deriving them would spend schedule on nothing.

### 7.2 Where this project differs, and one place it does not

**The difference is the runtime gain path, not the filter.** The four band gains in `fir_multiband` are a hardware register in the PL core, at AXI4-Lite offsets `0x50` and `0x54` (`report/hardware_interface_spec.md`), with a Q7.8 step of 1/256 ≈ 0.034 dB. They are written at runtime from the board's Python side, so changing a band gain takes a register write — no resynthesis, no rebuild, no reboot. The fitting interface is a Jupyter notebook of four sliders (`board/notebooks/w5_tuning_ui.ipynb`) that hands values to the streaming process through a shared-memory file; closing or restarting it does not interrupt audio.

In every other platform in the table, the parameter decides one of two things. Where runtime control exists, it lives on a CPU (openMHA) or on an MCU reached over BLE (Tympan). Where the control exists in hardware, the parameters are synthesis-time constants (the two RTL projects and the IFIR paper). This project keeps the tunable register inside the programmable logic alongside the audio path and leaves the CPU with only the write. That combination is what the search did not find.

One scope note on that claim: the register sits in the live audio path and the per-block self-check recomputes with the gains each block actually used, so the runtime path is verified numerically. It is also verified by ear: the 66 s run described in section 5.4 changed the gains away from flat while audio was flowing, and the operator heard the change. The claim in this section is about where the knob lives and how it is turned — the audible result is what that run establishes.

**The place it does not differ is the compressor.** Every real WDRC in the table — openMHA, Tympan, and the `dennisongit` RTL — carries two things this project's compressor does not:

- envelope detection (peak or RMS), and
- attack and release time constants (attack roughly 2–15 ms, release roughly 40–150 ms).

The compressor here is per-sample and stateless. Strictly speaking it is a **memoryless compressor, not WDRC**.

This is stated rather than left to be discovered, because the graders have openMHA and Tympan in hand and anyone who knows the field will ask. It is a deliberate scope decision: envelope detection plus smoothing means a second state set per band and several days the schedule does not have, and it would invalidate the bit-exact self-check the current evidence rests on. The consequence is bounded. The runtime-gain argument above does not depend on the compressor being WDRC, and the fixed-point and hardware results in sections 4 and 5 are measured against a reference implementation of the same memoryless rule, so they remain valid. What is *not* claimed is that this project implements a complete hearing-aid compression model.

### 7.3 Transfer beyond hearing assistance

The mechanism demonstrated here is a runtime-tunable multiband gain stage in programmable logic with a CPU-side control plane. That description also fits two larger markets, and the migration argument carries the "application value" half of the rubric.

**Conferencing.** A conference endpoint already runs multiband processing to equalise speaker and room. Moving it into the PL frees the application processor and bounds the latency, which matters more here than in a hearing aid because the same buffer also carries the far-end signal.

**Automotive.** Cabin audio needs per-zone band shaping that can change while the vehicle is running — seat position, window state and speed all move the target curve. That is exactly the runtime-register path used here, and automotive also wants the determinism the PL provides.

Neither migration is implemented. Both are argued from the same mechanism; the argument is that the mechanism, not the hearing-aid application, is the transferable part.

### 7.4 What this is worth on the rubric

The rubric splits novelty into three tiers: original work; a significant measured improvement in a key metric (≥30%, with measurements); or routine optimisation. This project does not claim the first tier. It sits in the second: the same-chip ARM-versus-PL speedup was re-measured on the board on 2026-10-23 at **16.7×** against the delivered 36-cycle core — 6.117 µs per sample for the ARM against 0.367 µs for the core (section 3). The earlier 49-cycle bit measured 12.5× and the 31-cycle bit 19.5×. Both numbers are far past 30%. The measurement is on the target chip rather than projected. The empty cell in the table above supports that claim; it is not the claim itself.

## 8. Reproduction

From the repository root, create a Python environment and run:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-python.txt
python src/python/multiband_baseline.py --taps 193 --repeat 5
python src/python/export_coefficients.py --taps 193
python src/python/export_golden.py --taps 193
```

The HLS C-simulation flow is documented in `build/hls/run_csim.tcl` and the README. It requires the matching Vitis HLS installation. Before publishing the comparison, record the tool version, exact build flags, test input, output filenames, sample alignment, SNR, and transparent-mode bit-exact check.

## 9. Remaining work before submission

1. **Toolchain version, and why it is not the recommended one.** The competition guide
   recommends Vivado/Vitis 2026.1 or 2025.2 and permits other versions provided the report
   states the reason and the scripts remain reproducible. This project uses **PYNQ image
   2.7.0 with Vivado and Vitis HLS 2020.2**, and every board measurement and synthesis
   record in the repository was produced with that pair. The reason is that the PYNQ 2.7.0
   image, its overlay-loading flow and the board's `audio_codec_ctrl` IP all belong to that
   tool generation; moving to 2025.2 would require a PYNQ image that has not been validated
   on this board, plus a regeneration of the audio IP and a re-verification of timing,
   bitstream and the 600 s real-time run. Reproducibility does not depend on where the
   tools are installed: `rebuild.sh` takes `VIVADO_BIN` and `VITIS_HLS_BIN`, and the only
   version-specific configuration is `HLS_CFLAGS` together with
   `build/hls/directives/`. See `BUILD_GUIDE.md`.
2. The 193-tap C simulation was rebuilt from source on 2026-09-30 with the documented toolchain and compared against the regenerated Python golden data; the record is in section 6 and the raw outputs are in `data/results/`.
3. ~~Produce the same-input Python/HLS/RTL comparison.~~ **Done in section 4.1.** The scope is the front end only; the RTL has no DRC, per-band gain or AXI shell, and has not run on the board.
4. Choose the final compressor parameter set. The measurements bound the choice — set 1 narrows the frame-level dynamic range by 1.9 dB, set 6 by 6.5 dB — but which one ships is a design preference (a heavier setting makes the effect visible at the cost of a larger change to the timbre), not something the measurements decide. The same setting must be written back into the board script and used for the video and the poster.
5. Record the demonstration video from the board evidence, review the English poster, and run a clean-machine reproduction.
6. ~~Verify the per-band gain by ear.~~ **Done on 2026-10-15.** The register path was already verified numerically; the ear check was then made with the gains set away from flat inside one continuous 66 s live run (six live writes, one band alternating 0.0 ↔ −41.9 dB), with the self-check still bit-exact across eight gain changes and the operator reporting several audible changes. See section 5.4; raw output in `data/results/fir_gain_ab_run_20261015.txt`.
7. Board and repository copies are in sync as of 2026-10-15: `mic_level.py` and `mic_live.py` were md5-checked and are already byte-identical (both use the 200 threshold), and the 2026-10-13 change to `board/scripts/audio_stream.cpp` was comments only, so `libaudio_stream.so` needs no rebuild.

## 10. Project artifacts

- Python reference: `src/python/multiband_baseline.py`
- Fixed board input generator: `src/python/export_board_input.py`
- Compressor transfer-curve figure: `src/python/plot_drc_curve.py` → `data/figures/drc_transfer_curve.png`
- Spectrum waterfall utility: `src/python/plot_spectrum_waterfall.py`
- Shared filter design: `src/python/band_design.py`
- HLS core and testbench: `src/hls/fir_multiband.cpp`, `src/hls/fir_tb.cpp`
- Current Python metrics: `data/results/baseline_metrics.md`
- HLS implementation metrics: `data/results/impl_metrics.md`
- On-board audio-loop run: `data/results/fir_audio_loop_run_20261006.txt`
- On-board real-time duplex run: `data/results/fir_live_run_20261015.txt`
- Reference-overlay metrics: `data/results/reference_overlay_metrics.md`
- Collaboration record: `report/llm_collab_log/`
- Skill package: `skill/`
