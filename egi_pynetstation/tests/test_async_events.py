"""Tests for the built-in asynchronous event sender.

send_event() must be safe to call directly from a PsychoPy flip callback:
it captures the timestamp on the calling thread and returns immediately,
without touching the socket. The same call is equally safe from ordinary
experiment code, so both usages can be mixed freely.
"""

import importlib
import time
import types


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
    ns._start_event_sender()
    try:
        ns.send_event(start=12.5, event_type='stm+')
        ns.flush_events()
    finally:
        ns._stop_event_sender()
    assert captured == [12.5]


def test_wait_true_overrides_the_default():
    ns = make_station()
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


def test_non_blocking_is_the_only_default():
    """One knob: send_event() never blocks unless asked to.

    A blocking socket write inside a flip callback is the failure mode this
    package exists to avoid, so the safe behaviour must not require the user
    to know a keyword argument. connect() has no competing setting.
    """
    import inspect
    assert inspect.signature(NetStation.send_event).parameters['wait'].default is False
    assert 'async_events' not in inspect.signature(NetStation.connect).parameters


def test_send_event_does_not_block_by_default():
    """Plain send_event() must not wait for the amplifier response."""
    ns = make_station(write_delay=0.05)
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


def test_sender_starts_on_demand_if_connect_did_not_start_it():
    """A non-blocking send must work even with no sender running yet."""
    ns = make_station(write_delay=0.05)
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
    try:
        for _ in range(5):
            ns.send_event(event_type='stm+', wait=False)
        ns.flush_events()
        assert len(ns.writes) == 5
        assert ns._event_thread is not None
    finally:
        ns._stop_event_sender()


def test_wait_true_returns_the_eci_response():
    ns = make_station()
    result = ns.send_event(event_type='stm+', wait=True)
    assert result is not None
    assert ns.writes


def test_queued_events_are_flushed_at_interpreter_exit():
    """Async by default means an unclean exit must not lose events."""
    ns = make_station(write_delay=0.01)
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
    ns._start_event_sender()
    ns.flush_events()
    ns._stop_event_sender()
    # Re-running the hook after shutdown must be harmless.
    ns._flush_at_exit()
    atexit.unregister(ns._flush_at_exit)


def test_flip_callback_and_direct_calls_can_be_mixed():
    """The realistic usage pattern: callOnFlip plus plain calls.

    An experiment marks visual onsets via win.callOnFlip(ns.send_event, ...)
    and marks other things -- responses, trial boundaries -- with ordinary
    send_event() calls. Both must stay fast, keep their own call-time
    timestamps, and preserve order.
    """
    ns = make_station(write_delay=0.005)
    captured = []
    real_send = ns._send_event_now
    ns._send_event_now = lambda **kw: (
        captured.append((kw['event_type'], kw['start'])), real_send(**kw)
    )[1]
    ns._start_event_sender()
    try:
        spans = []
        for _ in range(20):
            # Stands in for the flip callback.
            start = time.monotonic()
            ns.send_event(event_type='stim')
            spans.append(time.monotonic() - start)
            time.sleep(0.001)
            # Stands in for a response marker from the main loop.
            start = time.monotonic()
            ns.send_event(event_type='resp')
            spans.append(time.monotonic() - start)
            time.sleep(0.001)
        ns.flush_events()
    finally:
        ns._stop_event_sender()

    assert len(captured) == 40
    # Neither path blocks, even though each socket write takes 5 ms.
    assert max(spans) < 0.005
    # Order is preserved and timestamps strictly increase across both paths.
    assert [c[0] for c in captured] == ['stim', 'resp'] * 20
    stamps = [c[1] for c in captured]
    assert all(b > a for a, b in zip(stamps, stamps[1:]))
    assert not ns.event_errors()
