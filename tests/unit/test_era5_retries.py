"""ERA5 reads are retried after a transient network failure, never after a month that is not published yet."""

import pytest

from snowagent.ingest import era5


def test_transient_failure_is_retried_then_succeeds():
    calls, waits = [], []

    def flaky(x):
        calls.append(x)
        if len(calls) < 3:
            raise RuntimeError("Response payload is not completed")
        return x * 2

    assert era5.with_retries(flaky, 21, sleep=waits.append) == 42
    assert len(calls) == 3 and waits == [10.0, 20.0]


def test_gives_up_after_the_last_attempt():
    waits = []

    def broken():
        raise TimeoutError("FSTimeoutError")

    with pytest.raises(TimeoutError):
        era5.with_retries(broken, retries=2, sleep=waits.append)
    assert waits == [10.0, 20.0]


def test_unpublished_month_is_not_retried():
    waits = []

    def missing():
        raise FileNotFoundError("not on the mirror yet")

    with pytest.raises(FileNotFoundError):
        era5.with_retries(missing, sleep=waits.append)
    assert waits == []
