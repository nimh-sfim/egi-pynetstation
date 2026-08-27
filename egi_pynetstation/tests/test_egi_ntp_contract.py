#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""The exact NTP surface this package depends on."""

import inspect
import socket

import pytest

from egi_pynetstation import egi_ntp
from egi_pynetstation.NetStation import NetStation


REQUIRED_STATS_ATTRS = ('offset', 'delay', 'tx_time')


def test_request_accepts_the_arguments_we_pass():
    params = inspect.signature(egi_ntp.NTPClient.request).parameters

    for name in ('host', 'version', 'timeout'):
        assert name in params, f'egi_ntp.request lost the {name} argument'


def test_egi_ntp_default_timeout_is_still_what_we_compensate_for():
    default = inspect.signature(egi_ntp.NTPClient.request).parameters[
        'timeout'
    ].default

    assert default == 5, (
        f'egi_ntp default timeout is now {default}, not 5 s. '
        'Re-check NetStation._ntp_timeout and _auto_drift_join_timeout.'
    )


def test_fork_retains_ntplib_defaults_and_helpers():
    request = inspect.signature(egi_ntp.NTPClient.request).parameters

    assert request['version'].default == 2
    assert egi_ntp.NTPPacket().version == 2
    assert egi_ntp.mode_to_text(3) == 'client'
    assert egi_ntp.stratum_to_text(2) == 'secondary reference (2)'
    assert egi_ntp.NTP.NTP_DELTA == egi_ntp.NTP_DELTA


def test_vendored_fork_retains_the_upstream_mit_notice():
    source = inspect.getsource(egi_ntp)

    assert 'The MIT License (MIT)' in source
    assert 'Copyright (C) 2009-2015 Charles-Francois Natali' in source


@pytest.mark.parametrize('attr', REQUIRED_STATS_ATTRS)
def test_response_carries_the_fields_the_drift_model_fits(attr):
    assert hasattr(egi_ntp.NTPStats, attr), (
        f'egi_ntp.NTPStats no longer provides {attr}'
    )


def test_system_to_ntp_time_is_importable_and_monotonic():
    earlier = egi_ntp.system_to_ntp_time(1_000_000.0)
    later = egi_ntp.system_to_ntp_time(1_000_001.0)
    assert later > earlier
    assert later - earlier == pytest.approx(1.0)
    assert egi_ntp.ntp_to_system_time(earlier) == pytest.approx(1_000_000.0)


def test_package_no_longer_depends_on_ntplib():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    pyproject = (root / 'pyproject.toml').read_text(encoding='utf-8')
    setup_py = (root / 'setup.py').read_text(encoding='utf-8')

    assert 'ntplib' not in pyproject
    assert 'ntplib' not in setup_py


def test_the_timeout_we_use_is_shorter_than_client_default():
    station = NetStation('127.0.0.1', 55513)
    client_default = inspect.signature(
        egi_ntp.NTPClient.request
    ).parameters['timeout'].default

    assert station._ntp_timeout < client_default


def test_windows_uses_precise_wall_and_performance_counter(monkeypatch):
    monkeypatch.setattr(egi_ntp, 'WINDOWS', True)
    monkeypatch.setattr(egi_ntp, '_windows_precise_system_time',
                        lambda: 123.25)
    monkeypatch.setattr(egi_ntp.time, 'perf_counter', lambda: 456.5)

    assert egi_ntp.precise_system_time() == 123.25
    assert egi_ntp.monotonic_time() == 456.5


def test_netstation_keeps_public_and_package_clock_domains_separate():
    station = NetStation('127.0.0.1', 55513)
    station._connected = True
    station._syncepoch = 100.0
    station._sync_monotonic = 1_000.0
    station._sync_python_monotonic = 5_000.0
    station._drift_correction = False

    assert station.time_at_capture(1_000.25) == pytest.approx(0.25)
    assert station.time_at_monotonic(5_000.25) == pytest.approx(0.25)


def test_client_uses_precise_clock_hooks_for_local_timestamps(monkeypatch):
    calls = {'sent': None}
    local_times = iter([100.0, 100.020])
    monotonic_times = iter([42.020])

    class FakeSocket:
        def settimeout(self, timeout):
            self.timeout = timeout

        def sendto(self, data, sockaddr):
            calls['sent'] = data

        def recvfrom(self, size):
            request = egi_ntp.NTPPacket()
            request.from_data(calls['sent'])

            reply = egi_ntp.NTPPacket(version=3, mode=4)
            reply.orig_timestamp = request.tx_timestamp
            reply.recv_timestamp = egi_ntp.system_to_ntp_time(105.001)
            reply.tx_timestamp = egi_ntp.system_to_ntp_time(105.002)
            return reply.to_data(), ('10.10.10.51', 123)

        def close(self):
            pass

    monkeypatch.setattr(
        egi_ntp.socket,
        'getaddrinfo',
        lambda host, port: [
            (socket.AF_INET, socket.SOCK_DGRAM, 0, '', ('10.10.10.51', 123))
        ],
    )
    monkeypatch.setattr(
        egi_ntp.socket,
        'socket',
        lambda family, kind: FakeSocket(),
    )
    monkeypatch.setattr(egi_ntp, 'precise_system_time',
                        lambda: next(local_times))
    monkeypatch.setattr(egi_ntp, 'monotonic_time',
                        lambda: next(monotonic_times))
    monkeypatch.setattr(egi_ntp.time, 'monotonic', lambda: 84.020)

    response = egi_ntp.NTPClient().request('10.10.10.51', version=3)

    assert response.orig_time == pytest.approx(100.0)
    assert response.dest_time == pytest.approx(100.020)
    assert response.local_time == pytest.approx(100.020)
    assert response.monotonic_time == pytest.approx(42.020)
    assert response.python_monotonic_time == pytest.approx(84.020)
    assert response.delay == pytest.approx(0.019, abs=1e-6)
    assert response.offset == pytest.approx(4.9915, abs=1e-6)
