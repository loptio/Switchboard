"""Tests for the SDK-free cron helpers (no DB, no clock).

compute_next_run is also covered via the scheduler tests; here we pin
validate_cron (used by the API to reject bad cron strings with a 400) and the
strictly-after / timezone behaviour directly.
"""

from datetime import datetime, timezone

import pytest

import cronutil

T0 = datetime(2026, 6, 7, 10, 0, tzinfo=timezone.utc)


def test_validate_cron_accepts_valid():
    cronutil.validate_cron("0 6 * * *", "UTC")  # no raise
    cronutil.validate_cron("*/15 * * * *", "Asia/Shanghai")


@pytest.mark.parametrize("bad", ["", "not a cron", "0 6 * *", "60 6 * * *"])
def test_validate_cron_rejects_bad(bad):
    with pytest.raises(ValueError):
        cronutil.validate_cron(bad, "UTC")


def test_validate_cron_rejects_bad_timezone():
    with pytest.raises(ValueError):
        cronutil.validate_cron("0 6 * * *", "Mars/Phobos")


def test_compute_next_run_strictly_after_and_utc():
    assert cronutil.compute_next_run("0 6 * * *", "UTC", T0) == datetime(
        2026, 6, 8, 6, 0, tzinfo=timezone.utc
    )
    # 06:00 Asia/Shanghai == 22:00Z the previous day.
    assert cronutil.compute_next_run("0 6 * * *", "Asia/Shanghai", T0) == datetime(
        2026, 6, 7, 22, 0, tzinfo=timezone.utc
    )
