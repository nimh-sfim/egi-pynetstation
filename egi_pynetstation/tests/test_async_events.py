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


def test_async_is_the_connect_default():
    """Visual experiments are the primary use, so async is the default.

    A blocking socket write inside a flip callback is the failure mode this
    package exists to avoid, so the safe behaviour should not require the
    user to know a keyword argument.
    """
    import inspect
    signature = inspect.signature(NetStation.connect)
    assert signature.parameters['async_events'].default is True


def test_send_event_does_not_block_by_default_in_async_mode():
    """With async on, plain send_event() must not wait for the response."""
    ns = make_station(write_delay=0.05)
    ns._async_events = True
    ns._start_event_sender()
    try:
        start = time.monotonic()
        result = ns.send_event(event_type='stm+')   # no wait= argument
        elapsed = time.monotonic() - start
        assert result is None
        assert elapsed < 0.01
    finally:
        ns.flush_events()
        ns._stop_event_sender()


def test_wait_false_works_even_when_the_default_is_synchronous():
    """The two settings must compose in both directions.

    async_events is only the *default* policy for wait. Passing wait=False
    explicitly has to work on a synchronous connection too, which means the
    sender thread starts on first use rather than only at connect().
    """
    ns = make_station(write_delay=0.05)
    ns._async_events = False              # synchronous connection default
    assert ns._event_thread is None       # no sender started yet
    try:
        start = time.monotonic()
        result = ns.send_event(event_type='stm+', wait=False)
        elapsed = time.monotonic() - start
        assert result is None
        assert elapsed < 0.01
        assert ns._event_thread is not None, 'sender should start on demand'
        ns.flush_events()
        assert len(ns.writes) == 1
    finally:
        ns._stop_event_sender()


def test_lazy_sender_start_is_idempotent():
    ns = make_station()
    ns._async_events = False
    try:
        for _ in range(5):
            ns.send_event(event_type='stm+', wait=False)
        ns.flush_events()
        assert len(ns.writes) == 5
        assert ns._event_thread is not None
    finally:
        ns._stop_event_sender()


def test_explicit_sync_mode_still_returns_the_eci_response():
    ns = make_station()
    ns._async_events = False
    result = ns.send_event(event_type='stm+')
    assert result is not None
    assert ns.writes


def test_queued_events_are_flushed_at_interpreter_exit():
    """Async by default means an unclean exit must not lose events."""
    ns = make_station(write_delay=0.01)
    ns._async_events = True
    ns._start_event_sender()
    try:
        for _ in range(5):
            ns.send_event(event_type='stm+')
        assert ns.pending_events() > 0
        # Simulate interpreter shutdown without disconnect().
        ns._flush_at_exit()
        assert ns.pending_events() == 0
        assert len(ns.writes) == 5
    finally:
        ns._stop_event_sender()


def test_stopping_the_sender_unregisters_the_exit_hook():
    import atexit
    ns = make_station()
    ns._async_events = True
    ns._start_event_sender()
    ns.flush_events()
    ns._stop_event_sender()
    # Re-running the hook after shutdown must be harmless.
    ns._flush_at_exit()
    atexit.unregister(ns._flush_at_exit)
