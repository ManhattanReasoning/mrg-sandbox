"""Record effective clock settings for cloud build feedback."""

import json
import warnings
from pathlib import Path


def write_clock_config(build_dir, sys_clk_freq, timing_target_mhz):
    """Best effort only: reporting must not change build success."""
    try:
        path = Path(build_dir) / "build-clock-config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "sys_clk_freq_hz": sys_clk_freq,
                    "timing_target_mhz": timing_target_mhz,
                }
            )
        )
    except OSError as exc:
        warnings.warn(f"Could not save build clock settings: {exc}", stacklevel=2)
