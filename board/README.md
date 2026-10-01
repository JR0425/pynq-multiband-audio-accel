# Board Artifacts and Status

## Files in this repository

- `overlay/ps_only.bit` and its `.hwh` metadata: reference/playback overlay artifacts. They are not identified as containing the custom multiband HLS core.
- `notebooks/w2_audio_playback.ipynb`: base-overlay audio playback milestone notebook.
- `notebooks/test_multiband.ipynb`: board-side multiband test notebook; confirm its overlay interface against the final design before use.
- `scripts/verify_audio_playback.py`: base-overlay audio playback and capture check.
- `scripts/test_overlay_load.py`: overlay load and IP inventory check.
- `scripts/verify_fir_hw_e2e.py`: DMA FIR test procedure for an overlay exposing the expected FIR DMA. Its default `fir_accel2.bit` and `filter.fir_dma` are not included in this repository.

## Evidence boundary

`data/results/reference_overlay_metrics.md` records the test of a separate open-source 27-tap FIR reference overlay. That result validates a board-side DMA path but does not measure the custom HLS multiband core. The repository currently has no identified custom-core bitstream or end-to-end board measurement.

Before running an overlay test, confirm the bitstream, `.hwh`, DMA/IP names, board image version, and expected sample format as a set. Do not substitute `ps_only.bit` into `verify_fir_hw_e2e.py`; it does not expose the FIR DMA expected by that script.
