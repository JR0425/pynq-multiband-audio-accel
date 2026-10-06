# PYNQ Audio Project Skill Package

This folder collects reusable prompts, testbench templates, validation scripts, and troubleshooting notes from the PYNQ audio project.

## Format

Each note follows the official AMD Agent Skill (`SKILL.md`) conventions published in the
`Xilinx/ross-ai-assistant` package:

- **YAML frontmatter** with `name`, a trigger-phrase-rich `description`, `license`,
  `compatibility`, and `metadata.version`. The `description` is the field an agent matches
  against, so it names the symptoms and phrasings that should pull the note in — the same
  technique the official skills use.
- **An explicit scope block** (`什么时候用` / `什么时候别用`) so a note cannot be applied
  outside the situation it was measured in.
- **A revision table**, because the numbers in these notes change as the design changes.

Two things were deliberately *not* copied:

- The numbered `Step 0 … Step N` workflow skeleton. That shape fits the official skills,
  which drive a tool to produce an artifact. Most notes here are diagnostic: they record
  what went wrong, how it was identified, and what the measurement said. Forcing steps onto
  them would add structure without adding information.
- One directory per skill. The notes are referenced from dated collaboration-log entries and
  from scripts elsewhere in the repository; moving them would invalidate those links for no
  functional gain.

## Contents

- `pitfalls/`: board setup, audio playback, overlays, fixed-point types, HLS simulation,
  data consistency, and GitHub access.
- `prompts/`: prompts for audio algorithm design and AI-assisted work.
- `templates/`: HLS testbench and interface-specification templates.
- `checkers/`: audio-length, board connectivity, Jupyter file, and serial-port utilities.

## Index

| Skill name | Note | Scope |
|---|---|---|
| `pynq-direct-ethernet` | `pitfalls/pynq_direct_ethernet_windows.md` | Board-to-laptop link with no router |
| `pynq-serial-console` | `pitfalls/pynq_serial_console.md` | Serial console from Windows PowerShell |
| `pynq-shutdown-and-power` | `pitfalls/pynq_shutdown_and_power.md` | When software shutdown is required |
| `pynq-overlay-loading` | `pitfalls/pynq_overlay_loading.md` | `No Devices Found`; building a PS-only overlay |
| `pynq-audio-playback` | `pitfalls/pynq_audio_playback.md` | Sound out and audio in via the base overlay |
| `pynq-jupyter-files` | `pitfalls/pynq_jupyter_api.md` | File transfer and headless notebooks over HTTP |
| `pynq-matplotlib-font` | `pitfalls/pynq_matplotlib_font.md` | CJK and digits cannot both render on the board |
| `audio-data-consistency` | `pitfalls/audio_data_consistency.md` | Coefficients and data on the same sample rate |
| `audio-processing-bugs` | `pitfalls/audio_processing_bugs.md` | DC offset, variable slicing, FFT normalisation |
| `hls-csim-setup` | `pitfalls/hls_csim_setup.md` | Batch-mode C simulation |
| `hls-synthesis-directives` | `pitfalls/hls_synthesis_and_directives.md` | Which directive actually moves the numbers |
| `hls-fixed-point-types` | `pitfalls/hls_fixed_point_types.md` | `ap_fixed` versus float; bit-width choice |
| `windows-python-env` | `pitfalls/environment_setup_issues.md` | conda / pip failures on Windows |
| `github-network-access` | `pitfalls/github_network_issues.md` | Pushing when github.com is unreachable |
| `audio-algorithm-prompts` | `prompts/audio_algorithm_prompt.md` | Prompt patterns and their parameter traps |
| `hls-testbench-template` | `templates/hls_testbench_template.md` | Minimal C++ testbench skeleton |

## Tested environments and limits

All material in this repository was produced with one toolchain. Treat each note's stated version as part of its evidence:

- The board work, both the reference-overlay measurements and the custom-core integration, uses PYNQ-Z2 image 2.7.0. The reference-overlay results validate an open-source reference overlay; the custom-core results come from `board/overlay/fir.bit`.
- The recorded HLS implementation flow invokes Vitis HLS/Vivado 2020.2 and targets `xc7z020clg400-1`.
- This pair — PYNQ-Z2 image 2.7.0 with Vivado and Vitis HLS 2020.2 — is the declared project toolchain. No other combination has been used to produce results in this repository.

Do not combine results from these environments as if they came from one run. Before copying a command to another PYNQ image, board, or tool release, verify the version-specific APIs, IP metadata, and build output.

## Suggested workflow

1. Read the relevant note in `pitfalls/` and record the board image and tool versions.
2. Generate coefficients and golden data from `src/python/band_design.py` and `src/python/export_golden.py`.
3. Run the HLS C-simulation testbench and compare it with the matching Python golden output.
4. Verify transparent mode separately; it should reproduce the delayed input bit for bit.
5. Keep custom-core measurements separate from reference-overlay measurements and label whether a result is C simulation, out-of-context implementation, or board-level end-to-end timing.

## Current limitation

This package is a project-specific collection of scripts and lessons, not yet a standalone, version-independent PYNQ skill. The toolchain it was written against — PYNQ-Z2 image 2.7.0 with Vivado/Vitis HLS 2020.2 — is fixed and recorded above, so the instructions describe one reproducible setup rather than a moving target.
