# Project Progress Video Script

**Status:** capture-ready progress-video draft. It is not the final competition demonstration because the custom HLS core has not yet been integrated into the board audio path.

**Target length:** about 2 minutes.

**Evidence to capture:** Python baseline run and generated plot; the HLS implementation report; the separately labelled reference-overlay board test. Do not show the reference overlay as the custom multiband core.

## 0:00–0:20 | Project and goal

**Shot:** PYNQ-Z2 board and project title.

**Narration:** “This project explores multiband dynamic-range compression on a PYNQ-Z2. We are building a Python reference and an HLS FIR core so that the algorithm and the hardware implementation can be checked against the same input.”

## 0:20–0:55 | Algorithm and Python reference

**Shot:** Show the band equations and run `python src/python/multiband_baseline.py --taps 193 --repeat 5`. Display the resulting `multiband_comparison.png`.

**Narration:** “The current design uses three low-pass filters at 500, 1,000, and 2,000 hertz. Subtracting adjacent low-pass outputs forms four bands. The Python reference resamples this 44.1-kilohertz recording to 48 kilohertz and applies the same threshold and compression slope as the HLS design.”

## 0:55–1:25 | HLS core implementation evidence

**Shot:** Show the 193-tap row in `data/results/impl_metrics.md`; keep the OOC label visible.

**Narration:** “The custom HLS core has an out-of-context implementation result at a 100-megahertz constraint. The recorded design uses 3,962 LUTs, 5,514 flip-flops, 202 DSP blocks, and 51.5 BRAMs, with positive 1.624-nanosecond slack. This result covers the core by itself. It does not measure an integrated audio overlay.”

## 1:25–1:45 | Board-flow evidence

**Shot:** If showing the board, use the existing reference-overlay notebook and label the screen ‘open-source reference overlay, 27 taps’.

**Narration:** “We have also tested a separate open-source reference overlay on the PYNQ-Z2. That confirms a board-side control and data-transfer path, but it is not the custom multiband HLS core.”

## 1:45–2:00 | Current milestone

**Shot:** Show the submission checklist with the custom-core integration and end-to-end board measurement still open.

**Narration:** “The remaining hardware milestone is to integrate the custom core into the board audio path, demonstrate processed audio, and measure end-to-end latency. We will publish the final comparison after those measurements use the same input and alignment.”

## Final-video replacement required

After the custom core is integrated, replace the reference-overlay shot with a live custom-core demonstration. Add the measured board latency, throughput, and output comparison. Update the title and status narration so the video describes completed work rather than the current progress snapshot.
