import json

import pytest

from cloud_fpga_firmware.build_metadata import write_clock_config


def test_metadata_records_effective_settings(tmp_path):
    write_clock_config(tmp_path, 80_000_000, 90)
    assert json.loads((tmp_path / "build-clock-config.json").read_text()) == {
        "sys_clk_freq_hz": 80_000_000,
        "timing_target_mhz": 90,
    }


def test_metadata_write_failure_does_not_abort_build(tmp_path):
    target = tmp_path / "file"
    target.write_text("not a directory")
    with pytest.warns(UserWarning, match="Could not save"):
        write_clock_config(target, 50_000_000, 50)
