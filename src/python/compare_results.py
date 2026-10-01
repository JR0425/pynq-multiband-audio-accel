"""Compatibility entry point for the canonical Python/HLS comparator.

Use compare_golden_vs_hw.py directly for new work. This legacy filename remains
available so earlier instructions continue to work, but it now uses the same
alignment, SNR, error limits, and transparent-mode check as the canonical tool.
"""

import sys

from compare_golden_vs_hw import main


if __name__ == "__main__":
    sys.exit(main())
