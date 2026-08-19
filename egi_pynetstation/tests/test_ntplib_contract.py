#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""The exact surface of ntplib this package depends on.

``ntplib`` is the only runtime dependency and it sits directly in the
timing-critical measurement chain: every drift sample is an
``NTPClient.request()`` call, and the correction is fitted from the
``offset`` and ``delay`` it returns. Version 0.4.0 is the current PyPI
release and dates from May 2021 -- not evidence of a bug, but old enough
that a silent change under us would be expensive to diagnose from
timing data alone.

So rather than trusting the version number, these assert the handful of
things actually relied on. If a future release changes any of them, this
fails with a clear reason instead of the drift model quietly producing
worse fits.

Deliberately not covered: the wire protocol itself. That is ntplib's
job, and testing it here would only duplicate its own suite.
"""

import inspect

import ntplib
import pytest

from egi_pynetstation.NetStation import NetStation


# The whole dependency surface, in one place.
REQUIRED_STATS_ATTRS = ('offset', 'delay', 'tx_time')


def test_request_accepts_the_arguments_we_pass():
    """host, version=3, and a per-query timeout.

    The timeout matters especially: the package sets 2.0 s because
    ntplib's own default is 5 s, which on a 4-query burst against a dead
    server would block ~20 s -- longer than the sampler's join timeout
    was originally written to allow.
    """
    params = inspect.signature(ntplib.NTPClient.request).parameters

    for name in ('host', 'version', 'timeout'):
        assert name in params, f'ntplib.request lost the {name} argument'


def test_ntplib_default_timeout_is_still_what_we_compensate_for():
    """The package overrides this; if it changes, revisit _ntp_timeout."""
    default = inspect.signature(ntplib.NTPClient.request).parameters[
        'timeout'
    ].default

    assert default == 5, (
        f'ntplib default timeout is now {default}, not 5 s. '
        'Re-check NetStation._ntp_timeout and _auto_drift_join_timeout.'
    )


@pytest.mark.parametrize('attr', REQUIRED_STATS_ATTRS)
def test_response_carries_the_fields_the_drift_model_fits(attr):
    assert hasattr(ntplib.NTPStats, attr), (
        f'ntplib.NTPStats no longer provides {attr}'
    )


def test_system_to_ntp_time_is_importable_and_monotonic():
    """Used to build the ECI NTPClockSync argument."""
    from ntplib import system_to_ntp_time

    earlier = system_to_ntp_time(1_000_000.0)
    later = system_to_ntp_time(1_000_001.0)
    assert later > earlier
    assert later - earlier == pytest.approx(1.0)


def test_package_pins_a_deliberate_minimum():
    """An unpinned dependency in the timing chain is not acceptable."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    pyproject = (root / 'pyproject.toml').read_text(encoding='utf-8')
    assert 'ntplib = "^0.4.0"' in pyproject


def test_the_timeout_we_use_is_shorter_than_ntplibs_default():
    """Otherwise the join-timeout arithmetic understates the worst case."""
    station = NetStation('127.0.0.1', 55513)
    ntplib_default = inspect.signature(
        ntplib.NTPClient.request
    ).parameters['timeout'].default

    assert station._ntp_timeout < ntplib_default
