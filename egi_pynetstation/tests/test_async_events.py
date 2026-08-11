"""Tests for the built-in asynchronous event sender.

The point of async_events is that send_event() can be called directly from
a PsychoPy flip callback: it must capture the timestamp on the calling
thread and return immediately, without touching the socket.
"""

import importlib
import time
import types

import pytest


netstation_module = importlib.import_module('egi_pynetstation.NetStation')
NetStation = netstation_module.NetStation


def make_station(write_delay=0.0):
    writes = []

    def write(data):
        if write_delay:
            time.sleep(write_delay)
        writes.append(data)

    ns = NetStation('127.0.0.1', 55513)
    ns._connected = True
    ns._ntp_ip = '10.10.10.51'
    ns._socket = types.SimpleNamespace(
        write=write,
        read=lambda: b'Z',
        disconnect=lambda: None,
    )
    ns._sync_monotonic = time.monotonic()
    ns._syncepoch = time.time()
    ns._offset = 0.0
    ns._offset_mono = 0.0
    ns.writes = writes
    return ns


def test_async_send_event_returns_without_touching_socket():
    ns = make_station(write_delay=0.05)
    ns._async_events = True
    ns._start_event_sender()
    try:
        start = time.monotonic()
        ns.send_event(event_type='stm+')
        elapsed = time.monotonic() - start
        # A 50 ms socket write must not be on this thread's critical path.
        assert elapsed < 0.01
    finally:
        ns.flush_events()
        ns._stop_event_sender()


def test_async_timestamps_are_captured_at_call_not_at_send():
    """The whole point: the timestamp must describe the flip, not the send."""
    ns = make_station()
    captured = []
    ns._send_event_now = lambda **kwargs: captured.append(kwargs['start'])
    ns._async_events = True
    ns._start_event_sender()
    try:
        expected = []
        for _ in range(5):
            expected.append(ns.getTime())
            ns.send_event(event_type='stm+')
            time.sleep(0.02)
        ns.flush_events()
    finally:
        ns._stop_event_sender()

    assert len(captured) == 5
    # Each queued timestamp must match the moment of the call, not the
    # moment the worker got around to it.
    for want, got in zip(expected, captured):
        assert abs(got - want) < 0.005
    assert all(b > a for a, b in zip(captured, captured[1:]))


def test_explicit_start_is_preserved_through_the_queue():
    ns = make_station()
    captured = []
    ns._send_event_now = lambda **kwargs: captured.append(kwargs['start'])
    ns._async_events = True
    ns._start_event_sender()
    try:
        ns.send_event(start=12.5, event_type='stm+')
        ns.flush_events()
    finally:
        ns._stop_event_sender()
    assert captured == [12.5]


def test_wait_true_overrides_async_mode():
    ns = make_station()
    ns._async_events = True
    ns._start_event_sender()
    try:
        result = ns.send_event(event_type='stm+', wait=True)
        # A synchronous send returns the parsed ECI response.
        assert result is not None
        assert ns.writes, 'synchronous send should have hit the socket'
    finally:
        ns._stop_event_sender()


def test_sender_survives_a_failing_event_and_records_the_error():
    ns = make_station()
    calls = {'n': 0}

    def flaky(**kwargs):
        calls['n'] += 1
        if calls['n'] == 1:
            raise RuntimeError('boom')

    ns._send_event_now = flaky
    ns._async_events = True
    ns._start_event_sender()
    try:
        ns.send_event(event_type='bad_')
        ns.send_event(event_type='good')
        ns.flush_events()
    finally:
        ns._stop_event_sender()

    errors = ns.event_errors()
    assert len(errors) == 1
    assert 'boom' in errors[0]['error']
    assert errors[0]['event_type'] == 'bad_'
    # The second event must still have been sent.
    assert calls['n'] == 2


def test_synchronous_mode_is_the_default():
    ns = make_station()
    assert ns._async_events is False
    result = ns.send_event(event_type='stm+')
    assert result is not None
    assert ns.writes
