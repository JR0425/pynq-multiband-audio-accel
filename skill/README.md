# PYNQ Audio Project Skill Package

This folder collects reusable prompts, testbench templates, validation scripts, and troubleshooting notes from the PYNQ audio project.

## Contents

- `prompts/`: prompts for audio algorithm design and AI-assisted work.
- `templates/`: HLS testbench and interface-specification templates.
- `checkers/`: audio-length, board connectivity, Jupyter file, and serial-port utilities.
- `pitfalls/`: notes on board setup, audio playback, overlays, fixed-point types, HLS simulation, data consistency, and GitHub access.

## Tested environments and limits

The repository contains material from more than one environment. Treat each note's stated version as part of its evidence:

- The board work, both the reference-overlay measurements and the custom-core integration, uses PYNQ-Z2 image 2.7.0. The reference-overlay results validate an open-source reference overlay; the custom-core results come from `board/overlay/fir.bit`.
- The recorded HLS implementation flow invokes Vitis HLS/Vivado 2020.2 and targets `xc7z020clg400-1`.
- The team plan separately specifies PYNQ 3.1 and Vivado 2024.1 for the intended final environment. Compatibility of the existing overlay and HLS outputs with that combination has not been demonstrated in this repository.

Do not combine results from these environments as if they came from one run. Before copying a command to another PYNQ image, board, or tool release, verify the version-specific APIs, IP metadata, and build output.

## Suggested workflow

1. Read the relevant note in `pitfalls/` and record the board image and tool versions.
2. Generate coefficients and golden data from `src/python/band_design.py` and `src/python/export_golden.py`.
3. Run the HLS C-simulation testbench and compare it with the matching Python golden output.
4. Verify transparent mode separately; it should reproduce the delayed input bit for bit.
5. Keep custom-core measurements separate from reference-overlay measurements and label whether a result is C simulation, out-of-context implementation, or board-level end-to-end timing.

## Current limitation

This package is a project-specific collection of scripts and lessons, not yet a standalone, version-independent PYNQ skill. The final PYNQ image and Vivado/Vitis version need confirmation before the instructions can be validated as one reproducible setup.
