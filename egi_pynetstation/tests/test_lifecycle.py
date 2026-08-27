#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Recording-lifecycle and socket-transport hardening.

The distinction these tests protect is deliberate and easy to erode:

* **Event responses are tolerant.** A garbled reply to one marker returns
  a diagnostic record, because dropping a marker is better than ending a
  recording that is already underway.
* **Recording control is strict.** BeginRecording, EndRecording, the NTP
  sync, and the opening handshake always raise, whatever ``strict_eci``
  says. A silently refused BeginRecording produces the worst outcome the
  package can produce: a complete behavioural session, with local logs
  and markers, and no EEG.
"""

import importlib
import inspect
import socket
import threading
import time
import types

import pytest

from egi_pynetstation import egi_ntp

from egi_pynetstation.exceptions import (
    ECIFailure,
    ECINoRecordingDeviceFailure,
    InvalidECIResponse,
    NetStationLifecycleError,
    NetStationNoNTPIP,
    SocketException,
)
from egi_pynetstation.socket_wrapper import Socket

# `egi_pynetstation.NetStation` is re-exported as the class, so the module
# has to be imported explicitly for monkeypatching.
netstation_module = importlib.import_module('egi_pynetstation.NetStation')
exceptions_module = importlib.import_module('egi_pynetstation.exceptions')
NetStation = netstation_module.NetStation


class FakeResponse:
    """Stands in for NTPStats, including the clock readings it carries.

    The vendored client always attaches local_time/monotonic_time/
    python_monotonic_time, and NetStation now relies on that rather than
    falling back to a differently-framed clock read, so the fake has to
    carry them too.
    """

    offset = 0.0
    delay = 0.002
    tx_time = 0.0

    def __init__(self):
        self.local_time = time.time()
        self.monotonic_time = egi_ntp.monotonic_time()
        self.python_monotonic_time = time.monotonic()


def make_connected(monkeypatch, reply=b'Z', strict_eci=None):
    """A connected station whose amplifier always answers `reply`."""
    monkeypatch.setattr(
        netstation_module, 'NTPClient',
        lambda: types.SimpleNamespace(request=lambda *a, **k: FakeResponse()),
    )
    replies = {'value': reply}

    class FakeSocket:
        def __init__(self, *a, **k):
            pass

        def connect(self):
            pass

        def write(self, data):
            pass

        def read(self):
            return replies['value']

        def disconnect(self):
            pass

    monkeypatch.setattr(netstation_module, 'Socket', FakeSocket)
    ns = NetStation('10.10.10.42', 55513)
    ns.connect(ntp_ip='10.10.10.51', strict_eci=strict_eci)
    return ns, replies


# --- #1: recording control must fail loudly ------------------------------

@pytest.mark.parametrize('reply,expected', [
    (b'R', ECINoRecordingDeviceFailure),
    (b'F', ECIFailure),
    (b'\x00', InvalidECIResponse),
])
def test_begin_rec_raises_when_recording_is_refused(
    reply, expected, monkeypatch,
):
    ns, replies = make_connected(monkeypatch)
    replies['value'] = reply

    with pytest.raises(expected):
        ns.begin_rec()


@pytest.mark.parametrize('reply', [b'R', b'F', b'\x00'])
def test_refused_begin_rec_leaves_no_half_started_session(reply, monkeypatch):
    """The failure must not leave state that looks like a live recording."""
    ns, replies = make_connected(monkeypatch)
    replies['value'] = reply

    with pytest.raises(Exception):
        ns.begin_rec()

    assert ns.rec_start() is None
    assert ns._syncepoch is None
    assert ns._ntpsynced is False


def test_begin_rec_is_strict_even_when_strict_eci_is_off(monkeypatch):
    """strict_eci governs event replies, never recording control."""
    ns, replies = make_connected(monkeypatch, strict_eci=False)
    assert ns._strict_eci is False
    replies['value'] = b'R'

    with pytest.raises(ECINoRecordingDeviceFailure):
        ns.begin_rec()


def test_events_stay_tolerant_after_the_lifecycle_change(monkeypatch):
    """The other half of the split: a bad marker reply must not raise."""
    ns, replies = make_connected(monkeypatch)
    ns.begin_rec()
    replies['value'] = b'\x00'

    result = ns.send_event(event_type='stm+', wait=True)

    assert isinstance(result, dict)
    assert result['ok'] is False
    assert ns.getTime() is not None       # session still usable


def test_successful_begin_rec_is_unchanged(monkeypatch):
    ns, _ = make_connected(monkeypatch)

    ns.begin_rec()

    assert ns.rec_start() is not None
    assert ns._syncepoch is not None
    assert ns._ntpsynced is True
    assert ns.getTime() is not None


def test_failed_ntpsync_rolls_back_the_recording_start(monkeypatch):
    """BeginRecording succeeded but the epoch is unusable -- say so."""
    ns, _ = make_connected(monkeypatch)

    def boom(*a, **k):
        raise RuntimeError('NTP server unreachable')

    monkeypatch.setattr(ns, 'ntpsync', boom)
    with pytest.raises(RuntimeError):
        ns.begin_rec()

    assert ns.rec_start() is None


def test_begin_rec_without_ntp_ip_raises_before_touching_the_socket(
    monkeypatch,
):
    ns, _ = make_connected(monkeypatch)
    ns._ntp_ip = None

    with pytest.raises(NetStationNoNTPIP):
        ns.begin_rec()
    assert ns.rec_start() is None


@pytest.mark.parametrize('reply', [b'R', b'F', b'\x00'])
def test_end_rec_raises_when_the_stop_is_refused(reply, monkeypatch):
    """An operator must not walk away believing the recording stopped."""
    ns, replies = make_connected(monkeypatch)
    ns.begin_rec()
    replies['value'] = reply

    with pytest.raises(Exception):
        ns.end_rec()


def test_handshake_failure_is_reported(monkeypatch):
    """A refused opening handshake means there is no working session."""
    monkeypatch.setattr(
        netstation_module, 'NTPClient',
        lambda: types.SimpleNamespace(request=lambda *a, **k: FakeResponse()),
    )

    class FakeSocket:
        def __init__(self, *a, **k):
            pass

        def connect(self):
            pass

        def write(self, data):
            pass

        def read(self):
            return b'F'

        def disconnect(self):
            pass

    monkeypatch.setattr(netstation_module, 'Socket', FakeSocket)
    ns = NetStation('10.10.10.42', 55513)

    with pytest.raises(ECIFailure):
        ns.connect(ntp_ip='10.10.10.51')
    ns._stop_auto_drift_thread()


# --- #6: exceptions must stringify -------------------------------------

def _concrete_exceptions():
    """Exception classes that build their own message."""
    for name, cls in vars(exceptions_module).items():
        if not (inspect.isclass(cls) and issubclass(cls, BaseException)):
            continue
        params = list(inspect.signature(cls.__init__).parameters)[1:]
        if not params or any(
            p.kind is inspect.Parameter.VAR_POSITIONAL
            for p in inspect.signature(cls.__init__).parameters.values()
        ):
            continue          # base classes that take plain Exception args
        yield name, cls


SAMPLE_ARGS = {
    'transmitted': 1, 'expected': 2, 'arg': 'x', 'invalidcmd': 'x',
    'cmd': 'x', 'data': 'x', 'endian': 'x', 'noninteger': 'x',
    'o': b'x', 'bytearr': b'x' * 4, 'message': 'a message', 'status': b'\x00\x02',
}


@pytest.mark.parametrize(
    'name,cls', list(_concrete_exceptions()),
    ids=[n for n, _ in _concrete_exceptions()],
)
def test_exceptions_have_a_usable_string(name, cls):
    """str(err) was empty: the message never reached Exception.__init__.

    Internal code worked around it with getattr(err, 'message', str(err)),
    but tracebacks and ordinary callers just saw nothing.
    """
    params = list(inspect.signature(cls.__init__).parameters)[1:]
    err = cls(*[SAMPLE_ARGS[p] for p in params])

    assert str(err), f'{name} stringifies as empty'
    assert err.args and err.args[0] == str(err)
    assert err.message == str(err)      # kept for backwards compatibility


# --- #5: socket transport ------------------------------------------------

def test_read_on_a_disconnected_socket_raises_deliberately():
    """This branch used to call connect() on None -- an AttributeError."""
    sock = Socket('127.0.0.1', 55513)

    with pytest.raises(SocketException):
        sock.read()


def test_connect_timeout_applies_to_the_connect_call():
    """Set after connect(), the timeout did nothing for unreachable hosts."""
    sock = Socket('10.255.255.1', 55513)      # non-routable: hangs
    started = time.monotonic()

    with pytest.raises(OSError):
        sock.connect()

    assert time.monotonic() - started < Socket.connect_timeout + 3


def test_write_sends_a_large_payload_completely():
    """send() may write a prefix only; sendall() is required."""
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('127.0.0.1', 0))
    server.listen(1)
    port = server.getsockname()[1]
    payload = b'x' * 400_000        # more than one send() will take
    received = []

    def serve():
        conn, _ = server.accept()
        conn.settimeout(5)
        buf = b''
        while len(buf) < len(payload):
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf += chunk
        received.append(buf)
        conn.close()

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        sock = Socket('127.0.0.1', port)
        sock.connect()
        sock.write(payload)
        thread.join(timeout=10)
        assert received and received[0] == payload
    finally:
        sock.disconnect()
        server.close()


def test_a_broken_write_closes_the_socket():
    """After an unknown partial write the stream framing is unknown.

    Continuing to send would have the server parse later commands as a
    continuation of a truncated one, so the socket must be invalidated.
    """
    sock = Socket('127.0.0.1', 55513)

    class ExplodingSocket:
        def sendall(self, data):
            raise OSError('connection reset')

        def close(self):
            self.closed = True

    sock._socket = ExplodingSocket()
    with pytest.raises(OSError):
        sock.write(b'hello')

    assert sock._socket is None


# --- #2: one recording epoch per connection ------------------------------

def test_second_begin_rec_is_refused(monkeypatch):
    """A second epoch would re-base the clock under the existing model.

    begin_rec() re-runs the ECI clock sync, which moves the local event
    epoch. The drift model still holds samples whose elapsed times were
    measured from the previous origin, so a fit would span two coordinate
    systems.
    """
    ns, _ = make_connected(monkeypatch)
    ns.begin_rec()
    ns.end_rec()

    with pytest.raises(NetStationLifecycleError, match='already recorded'):
        ns.begin_rec()


def test_second_begin_rec_is_refused_before_sending_anything(monkeypatch):
    """The guard must come before BeginRecording reaches the amplifier."""
    ns, _ = make_connected(monkeypatch)
    ns.begin_rec()
    sent = []
    monkeypatch.setattr(
        ns, '_command',
        lambda *a, **k: sent.append(a[0]) or True,
    )

    with pytest.raises(NetStationLifecycleError):
        ns.begin_rec()
    assert sent == []


def test_repeated_ntpsync_is_refused_by_default(monkeypatch):
    ns, _ = make_connected(monkeypatch)
    ns.begin_rec()

    with pytest.raises(NetStationLifecycleError, match='already been performed'):
        ns.ntpsync()


def test_ntpsync_force_is_available_for_diagnostics(monkeypatch):
    ns, _ = make_connected(monkeypatch)
    ns.begin_rec()

    assert ns.ntpsync(force=True) is not None


def test_reconnect_starts_from_clean_state(monkeypatch):
    """Old drift samples must not survive into a new clock epoch."""
    ns, _ = make_connected(monkeypatch)
    ns.begin_rec()
    for index in range(6):
        offset = index * 30.0
        ns._record_ntp_drift_sample(
            FakeResponse(), source='test',
            local_time=ns._sync_monotonic + offset,
            monotonic_time=ns._sync_monotonic + offset,
        )
    assert ns.drift_history()
    ns.disconnect()

    # Retained after disconnect, so a finished run stays inspectable.
    assert ns.drift_history()

    ns.connect(ntp_ip='10.10.10.51')
    try:
        assert ns.drift_history() == []
        assert ns._syncepoch is None
        assert ns._ntpsynced is False
        assert ns._recording_started is False
        assert ns.eci_errors() == []
        assert ns.event_errors() == []
        ns.begin_rec()               # allowed again on a fresh connection
        assert ns.rec_start() is not None
    finally:
        ns.disconnect()


# --- #4: NTP sampling health --------------------------------------------

def make_flaky_ntp(monkeypatch, tmp_path):
    """A connected station whose NTP server can be made to fail."""
    failing = {'value': False}

    def request(*a, **k):
        if failing['value']:
            raise OSError('ntp unreachable')
        return FakeResponse()

    monkeypatch.setattr(
        netstation_module, 'NTPClient',
        lambda: types.SimpleNamespace(request=request),
    )

    class FakeSocket:
        def __init__(self, *a, **k):
            pass

        def connect(self):
            pass

        def write(self, data):
            pass

        def read(self):
            return b'Z'

        def disconnect(self):
            pass

    monkeypatch.setattr(netstation_module, 'Socket', FakeSocket)
    log = tmp_path / 'health.jsonl'
    ns = NetStation('10.10.10.42', 55513, error_log=str(log))
    ns.connect(ntp_ip='10.10.10.51')
    ns.begin_rec()
    return ns, failing, log


def read_records(log):
    if not log.exists():
        return []
    import json
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_total_ntp_outage_is_not_silent(monkeypatch, tmp_path):
    """The stall detector cannot see this: no samples means no fits.

    _note_drift_transition() counts *rejected* fits. A burst that fails
    entirely produces no sample at all, so no fit is attempted and
    drift_stalled stays False while the applied correction goes stale.
    """
    ns, failing, _ = make_flaky_ntp(monkeypatch, tmp_path)
    failing['value'] = True

    for _ in range(5):
        with pytest.raises(Exception):
            ns.sample_drift()

    summary = ns.session_summary()
    assert summary['ntp_sample_failures'] == 5
    assert summary['ntp_sampling_stale'] is True
    assert summary['drift_stalled'] is False       # the blind spot
    assert summary['ok'] is False
    ns.disconnect()


def test_outage_logs_once_not_once_per_interval(monkeypatch, tmp_path):
    """A long outage must not write a record every sampling interval."""
    ns, failing, log = make_flaky_ntp(monkeypatch, tmp_path)
    failing['value'] = True
    for _ in range(20):
        with pytest.raises(Exception):
            ns.sample_drift()

    failed = [r for r in read_records(log)
              if r['record'] == 'drift_sampling_failed']
    assert len(failed) == 1
    assert failed[0]['consecutive_failures'] >= 3
    ns.disconnect()


def test_recovery_is_recorded_and_clears_staleness(monkeypatch, tmp_path):
    ns, failing, log = make_flaky_ntp(monkeypatch, tmp_path)
    failing['value'] = True
    for _ in range(5):
        with pytest.raises(Exception):
            ns.sample_drift()

    failing['value'] = False
    ns.sample_drift()

    assert ns._ntp_consecutive_failures == 0
    assert ns.session_summary()['ntp_sampling_stale'] is False
    recovered = [r for r in read_records(log)
                 if r['record'] == 'drift_sampling_recovered']
    assert len(recovered) == 1
    assert recovered[0]['failures_during_outage'] == 5
    ns.disconnect()


def test_a_single_transient_failure_is_not_reported(monkeypatch, tmp_path):
    """One lost burst is noise, not an outage."""
    ns, failing, log = make_flaky_ntp(monkeypatch, tmp_path)
    failing['value'] = True
    with pytest.raises(Exception):
        ns.sample_drift()
    failing['value'] = False
    ns.sample_drift()

    records = [r['record'] for r in read_records(log)]
    assert 'drift_sampling_failed' not in records
    assert ns._ntp_consecutive_failures == 0
    assert ns._ntp_failure_count == 1        # still counted
    ns.disconnect()


def test_stale_sampling_flags_a_session_that_once_worked(monkeypatch, tmp_path):
    """Samples arrived, then stopped: ok must go False on age alone."""
    ns, _, _ = make_flaky_ntp(monkeypatch, tmp_path)
    ns.sample_drift()
    assert ns.session_summary()['ntp_sampling_stale'] is False

    # Last success recedes past max(2 * interval, drift_max_model_age).
    health = ns._ntp_sampling_health()
    ns._ntp_last_success_monotonic -= health['ntp_staleness_threshold'] + 60

    summary = ns.session_summary()
    assert summary['ntp_sampling_stale'] is True
    assert summary['ok'] is False
    ns.disconnect()


def test_cooperative_sampling_is_not_judged_stale(monkeypatch, tmp_path):
    """Only a background sampler is expected to keep itself current."""
    ns, _, _ = make_flaky_ntp(monkeypatch, tmp_path)
    ns.configure_auto_drift(background=False)
    ns._ntp_last_success_monotonic = None
    ns._ntp_failure_count = 5

    assert ns.session_summary()['ntp_sampling_stale'] is False
    ns.disconnect()


def test_health_fields_appear_in_clock_state(monkeypatch, tmp_path):
    ns, _, _ = make_flaky_ntp(monkeypatch, tmp_path)
    state = ns.clock_state()

    for key in ('ntp_sampling_expected', 'ntp_sample_failures',
                'ntp_consecutive_failures', 'ntp_seconds_since_success',
                'ntp_sampling_stale', 'ntp_last_error'):
        assert key in state, key
    ns.disconnect()


# --- #5c: connection setup is transactional ------------------------------

def make_failing_connect(monkeypatch, reply=b'Z'):
    """A station whose amplifier answers `reply`, tracking socket closes."""
    monkeypatch.setattr(
        netstation_module, 'NTPClient',
        lambda: types.SimpleNamespace(request=lambda *a, **k: FakeResponse()),
    )
    closes = []

    class FakeSocket:
        def __init__(self, *a, **k):
            pass

        def connect(self):
            pass

        def write(self, data):
            pass

        def read(self):
            return reply

        def disconnect(self):
            closes.append(True)

    monkeypatch.setattr(netstation_module, 'Socket', FakeSocket)
    return NetStation('10.10.10.42', 55513), closes


def eci_threads():
    return [t for t in threading.enumerate() if t.name.startswith('eci-')]


@pytest.mark.parametrize('reply,exc', [(b'F', ECIFailure),
                                       (b'\x00', InvalidECIResponse)])
def test_failed_handshake_leaves_no_partial_connection(
    reply, exc, monkeypatch,
):
    """Setup is all-or-nothing.

    A rejected handshake used to leave an open socket, a live sampler
    thread, and `_connected` set, so the object looked usable and the
    next call failed somewhere less obvious.
    """
    before = len(eci_threads())
    ns, closes = make_failing_connect(monkeypatch, reply=reply)

    with pytest.raises(exc):
        ns.connect(ntp_ip='10.10.10.51')

    assert ns._connected is False
    assert ns._ntp_ip is None
    assert closes                        # socket was closed
    assert len(eci_threads()) == before   # no thread left running


@pytest.mark.parametrize('kwargs', [
    {'drift_min_samples': 1},
    {'drift_window_minutes': -5},
])
def test_invalid_drift_argument_leaves_no_partial_connection(
    kwargs, monkeypatch,
):
    before = len(eci_threads())
    ns, closes = make_failing_connect(monkeypatch)

    with pytest.raises(ValueError):
        ns.connect(ntp_ip='10.10.10.51', **kwargs)

    assert ns._connected is False
    assert closes
    assert len(eci_threads()) == before


def test_connect_succeeds_after_a_failed_attempt(monkeypatch):
    """A failed setup must not poison the object for a later good one."""
    replies = {'value': b'F'}

    monkeypatch.setattr(
        netstation_module, 'NTPClient',
        lambda: types.SimpleNamespace(request=lambda *a, **k: FakeResponse()),
    )

    class FakeSocket:
        def __init__(self, *a, **k):
            pass

        def connect(self):
            pass

        def write(self, data):
            pass

        def read(self):
            return replies['value']

        def disconnect(self):
            pass

    monkeypatch.setattr(netstation_module, 'Socket', FakeSocket)
    ns = NetStation('10.10.10.42', 55513)

    with pytest.raises(ECIFailure):
        ns.connect(ntp_ip='10.10.10.51')

    replies['value'] = b'Z'
    ns.connect(ntp_ip='10.10.10.51')
    try:
        ns.begin_rec()
        assert ns.rec_start() is not None
        assert ns.getTime() is not None
    finally:
        ns.disconnect()


# --- connect() must deliver every drift option to its setter -------------

def test_every_drift_option_reaches_its_setter(monkeypatch):
    """Each connect() drift argument lands on the setting it names.

    connect() forwards these through a single dict rather than seventeen
    positional arguments. The failure this guards against is silent: a
    transposed or dropped argument leaves the setting at its default, the
    session runs, and only the timing is wrong. Every value below is
    deliberately distinct from both its default and the others, so a swap
    between any two is visible here.
    """
    ns, _ = make_connected(monkeypatch)
    ns.disconnect()

    ns.connect(
        ntp_ip='10.10.10.51',
        drift_correction=False,
        drift_min_samples=7,
        drift_min_span=120.0,
        drift_max_delay=0.011,
        drift_max_residual=0.004,
        drift_window_minutes=9.0,
        drift_samples=6,
        drift_sample_spacing=0.07,
        drift_slew=0.0003,
        drift_max_model_age=450.0,
        auto_drift=True,
        auto_drift_interval=21.0,
        auto_drift_min_pause=0.55,
        auto_drift_background=False,
    )
    try:
        state = ns.clock_state()
        assert state['drift_correction'] is False
        assert state['drift_min_samples'] == 7
        assert state['drift_min_span'] == 120.0
        assert state['drift_max_delay'] == 0.011
        assert state['drift_max_residual'] == 0.004
        assert state['drift_window'] == 9.0 * 60.0
        assert state['drift_samples_per_call'] == 6
        assert state['drift_slew'] == 0.0003
        assert state['drift_max_model_age'] == 450.0
        # Not surfaced by clock_state(), so read them directly rather than
        # leave the only two unpinned options in the set.
        assert ns._drift_sample_spacing == 0.07
        assert ns._auto_drift_enabled is True
        assert ns._auto_drift_interval == 21.0
        assert ns._auto_drift_min_pause == 0.55
        assert ns._auto_drift_background is False
    finally:
        ns.disconnect()


def test_drift_options_dict_rejects_an_unknown_key(monkeypatch):
    """A typo in the option dict fails loudly at connect time."""
    ns, _ = make_connected(monkeypatch)
    try:
        with pytest.raises(KeyError, match='unknown drift option'):
            ns._configure_and_handshake(
                '10.10.10.51', False, True, None,
                dict.fromkeys(NetStation._DRIFT_OPTION_KEYS) | {'slwe': 1},
            )
    finally:
        ns.disconnect()


def test_drift_options_dict_rejects_a_missing_key(monkeypatch):
    """Dropping an option is caught rather than left at its default."""
    ns, _ = make_connected(monkeypatch)
    try:
        options = dict.fromkeys(NetStation._DRIFT_OPTION_KEYS)
        del options['slew']
        with pytest.raises(KeyError, match='missing drift option'):
            ns._configure_and_handshake(
                '10.10.10.51', False, True, None, options,
            )
    finally:
        ns.disconnect()


# --- drift_settings() reports what is really in effect --------------------

def test_drift_settings_works_before_connect(monkeypatch):
    """Readable unconnected, which is the point: connect()'s signature
    defaults are all None, so the real defaults are otherwise hidden."""
    ns = NetStation('10.10.10.42', 55513)
    settings = ns.drift_settings()
    assert settings['drift_min_samples'] == 13
    assert settings['drift_min_span'] == 180.0
    assert settings['drift_window_minutes'] == 15.0
    assert settings['auto_drift_background'] is True


def test_drift_settings_reports_what_connect_applied(monkeypatch):
    """The report tracks the session, not the defaults."""
    ns, _ = make_connected(monkeypatch)
    ns.disconnect()
    ns.connect(
        ntp_ip='10.10.10.51',
        drift_min_samples=7,
        drift_window_minutes=9.0,
        auto_drift_background=False,
    )
    try:
        settings = ns.drift_settings()
        assert settings['drift_min_samples'] == 7
        assert settings['drift_window_minutes'] == 9.0
        assert settings['auto_drift_background'] is False
        # Untouched options still report the package default.
        assert settings['drift_min_span'] == 180.0
        assert settings['drift_max_delay'] == 0.010
    finally:
        ns.disconnect()


def test_drift_settings_reports_no_limit_as_zero(monkeypatch):
    """0 means "no limit" to connect(); None is only the internal form."""
    ns, _ = make_connected(monkeypatch)
    ns.disconnect()
    ns.connect(
        ntp_ip='10.10.10.51',
        drift_window_minutes=0,
        drift_max_model_age=0,
        auto_drift_background=False,
    )
    try:
        settings = ns.drift_settings()
        assert settings['drift_window_minutes'] == 0.0
        assert settings['drift_max_model_age'] == 0.0
    finally:
        ns.disconnect()


def test_drift_settings_covers_every_connect_drift_argument():
    """No drift argument of connect() may go unreported.

    The report exists to say what a session ran with. An option added to
    connect() and forgotten here would leave a silent hole in that.
    """
    import inspect

    reported = set(NetStation('10.0.0.1', 1).drift_settings())
    accepted = {
        name for name in inspect.signature(NetStation.connect).parameters
        if name.startswith(('drift_', 'auto_drift'))
    }
    assert accepted - reported == set()
    # The one documented extra: settable only via set_drift_stability().
    assert reported - accepted == {'drift_stall_after'}
