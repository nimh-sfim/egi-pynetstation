"""Tests for the JSON-lines ECI error log.

The log exists for post-hoc diagnosis, so the things it must not do are:
lose the failure an experiment is most likely to hit, omit the context
needed to explain it, or become a second failure itself.
"""

import importlib
import json
import time
import types
import warnings

import pytest


netstation_module = importlib.import_module('egi_pynetstation.NetStation')
NetStation = netstation_module.NetStation


class FakeResponse:
    def __init__(self):
        self.offset = 0.0
        self.delay = 0.002
        self.tx_time = 0.0


def make_station(tmp_path, monkeypatch, reply=b'Z', write_error=None):
    monkeypatch.setattr(
        netstation_module, 'NTPClient',
        lambda: types.SimpleNamespace(request=lambda *a, **k: FakeResponse()),
    )
    log = tmp_path / 'nested' / 'errors.jsonl'   # directory must be created
    ns = NetStation('127.0.0.1', 55513, error_log=str(log))
    ns._connected = True
    ns._ntp_ip = '10.10.10.51'

    def write(_data):
        if write_error is not None:
            raise write_error

    ns._socket = types.SimpleNamespace(
        write=write, read=lambda: reply, disconnect=lambda: None,
    )
    ns._sync_monotonic = time.monotonic()
    ns._syncepoch = time.time()
    ns._offset = 0.0
    ns._offset_mono = 0.0
    return ns, log


def read_records(log):
    # A log with nothing to say is never created at all.
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_async_send_failure_reaches_the_error_log(tmp_path, monkeypatch):
    """The most likely real failure must not be file-invisible.

    Asynchronous sending is the default, and an async failure cannot be
    raised to the caller. Before this, it went only to event_errors() and
    the Python logger, so an experimenter reading the log file afterwards
    would see nothing at all.
    """
    ns, log = make_station(
        tmp_path, monkeypatch, write_error=ConnectionError('amp went away'),
    )
    ns._start_event_sender()
    try:
        ns.send_event(event_type='stm+', label='trial 8')
        ns.flush_events()
    finally:
        ns._stop_event_sender()

    records = [r for r in read_records(log)
               if r['record'] == 'event_send_failure']
    assert len(records) == 1
    assert records[0]['event_type'] == 'stm+'
    assert records[0]['label'] == 'trial 8'
    assert 'amp went away' in records[0]['error']
    # And it is still reachable from the API.
    assert len(ns.event_errors()) == 1


def test_error_records_carry_a_clock_state_snapshot(tmp_path, monkeypatch):
    """Knowing the drift state at failure time is usually the explanation."""
    ns, log = make_station(tmp_path, monkeypatch, reply=b'\x00')
    for _ in range(5):
        ns._record_ntp_drift_sample(FakeResponse(), source='test')
    ns.send_event(event_type='stm+', wait=True)

    record = read_records(log)[-1]
    clock = record['clock']
    assert clock['drift_samples'] == 5
    for key in ('drift_rejected_fits', 'drift_model_age', 'sys_mono_skew',
                'predicted_ntp_offset', 'drift_correction'):
        assert key in clock


def test_response_errors_identify_the_event(tmp_path, monkeypatch):
    """cmd='EventData' alone cannot tell you which trial failed."""
    ns, log = make_station(tmp_path, monkeypatch, reply=b'\x00')
    ns.send_event(event_type='stm+', label='trial 42', wait=True)

    record = read_records(log)[-1]
    assert record['record'] == 'eci_response_error'
    assert record['cmd'] == 'EventData'
    assert record['event_type'] == 'stm+'
    assert record['label'] == 'trial 42'
    assert record['raw_hex'] == '00'


def test_a_broken_log_path_never_breaks_the_recording(tmp_path, monkeypatch):
    """Losing the log is bad; losing the recording is worse."""
    ns, _ = make_station(tmp_path, monkeypatch, reply=b'\x00')
    # /dev/null is a file, so nothing beneath it can be created.
    ns.set_error_log('/dev/null/nope/errors.jsonl')

    result = ns.send_event(event_type='stm+', wait=True)
    assert isinstance(result, dict) and result['ok'] is False
    # The station is still usable afterwards.
    assert ns.getTime() is not None


def test_log_directory_is_created(tmp_path, monkeypatch):
    ns, log = make_station(tmp_path, monkeypatch, reply=b'\x00')
    assert not log.parent.exists()
    ns.send_event(event_type='stm+', wait=True)
    assert log.exists()


def make_bare_station(tmp_path):
    """A station with no socket, for driving the drift model directly."""
    log = tmp_path / 'drift.jsonl'
    ns = NetStation('127.0.0.1', 55513, error_log=str(log))
    ns._connected = True
    # These tests advance a synthetic clock in exact 15-second steps. Using
    # the host's (potentially large) monotonic value as the origin makes
    # ``origin + 60 - origin`` round just below 60 on some runners, delaying
    # the minimum-span gate by one sample and making the fixture platform
    # dependent.
    ns._sync_monotonic = 0.0
    ns._syncepoch = time.time()
    ns._offset = 0.0
    ns._offset_mono = 0.0
    ns.set_drift_requirements(min_samples=5, min_span=60.0)
    ns.set_drift_model_options(max_residual=0.003, window_minutes=5)
    ns.set_drift_stability(stall_after=3)
    return ns, log


def feed(ns, index, offset):
    elapsed = index * 15.0
    ns._record_ntp_drift_sample(
        types.SimpleNamespace(offset=offset, delay=0.002, tx_time=0.0),
        source='test',
        local_time=ns._sync_monotonic + elapsed,
        monotonic_time=ns._sync_monotonic + elapsed,
    )
    ns._predict_ntp_offset(elapsed)
    # These tests drive the model directly instead of going through
    # sample_drift(), which is what normally flushes parked transition
    # records once the clock lock is released.
    ns._flush_pending_log_records()


def test_drift_model_engage_stall_and_recover_are_logged(tmp_path):
    """A stalled drift model is otherwise completely silent.

    Fits get refused, the last accepted slope keeps being extrapolated, and
    nothing raises until the timing error has already grown. In one
    observed hour-long run this went unnoticed for 15 minutes and cost
    about 17 ms. These records make it visible as it happens.
    """
    ns, log = make_bare_station(tmp_path)
    for index in range(60):
        elapsed = index * 15.0
        step = 0.05 if index >= 20 else 0.0     # 50 ms discontinuity
        feed(ns, index, 1e-5 * elapsed + step)

    records = [json.loads(line) for line in log.read_text().splitlines()]
    kinds = [r['record'] for r in records]
    assert kinds == [
        'drift_model_engaged',
        'drift_model_stalled',
        'drift_model_recovered',
    ]

    engaged, stalled, recovered = records
    assert engaged['elapsed'] == pytest.approx(60.0)
    # The stall is reported once, shortly after the step, not on every fit.
    assert stalled['drift_consecutive_rejections'] == 3
    assert stalled['drift_last_reject_reason'] == 'high_residual'
    assert stalled['elapsed'] > engaged['elapsed']
    # Recovery reports how long the model was blind.
    assert recovered['stall_duration'] > 0
    assert recovered['rejected_during_stall'] > 3
    assert ns.clock_state()['drift_stalled'] is False


def test_startup_rejections_do_not_report_a_stall(tmp_path):
    """Refusing to fit before there is evidence is normal, not a fault."""
    ns, log = make_bare_station(tmp_path)
    for index in range(3):                       # below min_samples
        feed(ns, index, 1e-5 * index * 15.0)

    assert ns._drift_rejected_fits > 0           # fits were refused
    assert read_records(log) == []


def test_stall_is_reported_once_not_per_sample(tmp_path):
    ns, log = make_bare_station(tmp_path)
    for index in range(20):
        feed(ns, index, 1e-5 * index * 15.0)
    for index in range(20, 40):                  # sustained discontinuity
        feed(ns, index, 1e-5 * index * 15.0 + 0.05 * (index % 2))

    records = [json.loads(line) for line in log.read_text().splitlines()]
    stalls = [r for r in records if r['record'] == 'drift_model_stalled']
    assert len(stalls) == 1, 'a stall must not be logged on every sample'


def test_background_sampling_needs_no_cooperation(tmp_path, monkeypatch):
    """background=True is the answer to 'what if nobody calls it?'"""
    ns, _ = make_station(tmp_path, monkeypatch)
    ns.configure_auto_drift(enabled=True, interval=0.02, background=True)
    try:
        deadline = time.monotonic() + 2.0
        while len(ns.drift_history()) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(ns.drift_history()) >= 3, 'nothing sampled on its own'
    finally:
        ns._stop_auto_drift_thread()


def test_enabling_auto_drift_and_never_calling_it_is_reported(
    tmp_path, monkeypatch
):
    """The silent failure: a schedule with nothing acting on it.

    configure_auto_drift() only records intent. With background=False,
    sample_drift_if_due() is the only thing that takes a sample, so an
    experiment that never calls it loses drift correction entirely and
    nothing else says so. Background sampling is the default precisely to
    avoid this, so the test has to opt back into the cooperative path.
    """
    ns, log = make_station(tmp_path, monkeypatch)
    ns.configure_auto_drift(enabled=True, interval=0.01, background=False)
    ns._sync_monotonic = time.monotonic() - 10.0           # 10 s elapsed
    ns._warn_if_undersampled()

    records = [r for r in read_records(log)
               if r['record'] == 'drift_undersampled']
    assert len(records) == 1
    assert records[0]['samples_collected'] == 0
    assert records[0]['samples_expected'] > 100


def test_no_undersampling_warning_when_sampling_happened(
    tmp_path, monkeypatch
):
    ns, log = make_station(tmp_path, monkeypatch)
    ns.configure_auto_drift(enabled=True, interval=1.0)
    ns._sync_monotonic = time.monotonic() - 10.0
    for _ in range(10):
        ns._record_ntp_drift_sample(FakeResponse(), source='test')
    ns._warn_if_undersampled()

    assert not [r for r in read_records(log)
                if r['record'] == 'drift_undersampled']


def test_auto_drift_can_be_configured_from_connect(tmp_path, monkeypatch):
    """One call should be able to set everything up."""
    monkeypatch.setattr(
        netstation_module, 'NTPClient',
        lambda: types.SimpleNamespace(request=lambda *a, **k: FakeResponse()),
    )
    monkeypatch.setattr(netstation_module, 'Socket', lambda *a, **k:
                        types.SimpleNamespace(
                            connect=lambda: None, write=lambda b: None,
                            read=lambda: b'Z', disconnect=lambda: None))
    ns = NetStation('127.0.0.1', 55513)
    ns.connect(
        ntp_ip='10.10.10.51',
        auto_drift=True,
        auto_drift_interval=15.0,
        auto_drift_min_pause=0.35,
        auto_drift_background=False,
    )
    try:
        assert ns._auto_drift_enabled is True
        assert ns._auto_drift_interval == 15.0
        assert ns._auto_drift_min_pause == 0.35
        assert ns._auto_drift_background is False
    finally:
        ns._stop_auto_drift_thread()


def test_connect_enables_auto_drift_and_background_by_default(
    tmp_path, monkeypatch
):
    """Auto-drift and background sampling are both on by default.

    Background sampling needs no cooperation from the experiment and has
    matched or beaten cooperative sampling on every validation run tried,
    so it is the default rather than something opted into.
    """
    monkeypatch.setattr(
        netstation_module, 'NTPClient',
        lambda: types.SimpleNamespace(request=lambda *a, **k: FakeResponse()),
    )
    monkeypatch.setattr(netstation_module, 'Socket', lambda *a, **k:
                        types.SimpleNamespace(
                            connect=lambda: None, write=lambda b: None,
                            read=lambda: b'Z', disconnect=lambda: None))
    ns = NetStation('127.0.0.1', 55513)
    ns.connect(ntp_ip='10.10.10.51')
    try:
        assert ns._auto_drift_enabled is True
        assert ns._auto_drift_background is True
        assert ns._auto_drift_thread is not None
    finally:
        ns._stop_auto_drift_thread()


def test_connect_can_opt_into_cooperative_sampling(tmp_path, monkeypatch):
    """False is the advanced path: no thread starts, nothing samples on
    its own, and the experiment must call sample_drift_if_due() itself."""
    monkeypatch.setattr(
        netstation_module, 'NTPClient',
        lambda: types.SimpleNamespace(request=lambda *a, **k: FakeResponse()),
    )
    monkeypatch.setattr(netstation_module, 'Socket', lambda *a, **k:
                        types.SimpleNamespace(
                            connect=lambda: None, write=lambda b: None,
                            read=lambda: b'Z', disconnect=lambda: None))
    ns = NetStation('127.0.0.1', 55513)
    ns.connect(ntp_ip='10.10.10.51', auto_drift_background=False)
    assert ns._auto_drift_enabled is True
    assert ns._auto_drift_background is False
    assert ns._auto_drift_thread is None


def test_connect_can_disable_auto_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(
        netstation_module, 'NTPClient',
        lambda: types.SimpleNamespace(request=lambda *a, **k: FakeResponse()),
    )
    monkeypatch.setattr(netstation_module, 'Socket', lambda *a, **k:
                        types.SimpleNamespace(
                            connect=lambda: None, write=lambda b: None,
                            read=lambda: b'Z', disconnect=lambda: None))
    ns = NetStation('127.0.0.1', 55513)
    ns.connect(ntp_ip='10.10.10.51', auto_drift=False)
    assert ns._auto_drift_enabled is False
    assert ns.sample_drift_if_due()['reason'] == 'disabled'


def test_removed_legacy_alias_is_gone():
    """2.0 drops resync_do_not_use_not_recommended()."""
    assert not hasattr(NetStation, 'resync_do_not_use_not_recommended')
    assert hasattr(NetStation, 'resync')


def test_session_summary_reflects_engagement_and_failures(tmp_path, monkeypatch):
    """One call should answer 'is this session healthy' without the caller
    combining clock_state() and event_errors() by hand."""
    ns, _ = make_station(tmp_path, monkeypatch)
    ns.set_drift_requirements(min_samples=3, min_span=5.0)

    before = ns.session_summary()
    assert before['ok'] is False
    assert before['drift_engaged'] is False

    for i in range(5):
        ns._record_ntp_drift_sample(FakeResponse(), source='test')
    after = ns.session_summary()
    assert after['drift_engaged'] is True or after['drift_samples'] == 5

    ns._event_errors.append({'error': 'boom'})
    with_error = ns.session_summary()
    assert with_error['ok'] is False
    assert with_error['event_send_failures'] == 1


# --- Regression tests for the 2026-08-12 audit ---------------------------

def _bare_station(monkeypatch):
    """A NetStation with the network stubbed out, ready to connect()."""
    monkeypatch.setattr(
        netstation_module, 'NTPClient',
        lambda: types.SimpleNamespace(request=lambda *a, **k: FakeResponse()),
    )
    monkeypatch.setattr(
        netstation_module, 'Socket', lambda *a, **k: types.SimpleNamespace(
            connect=lambda: None, write=lambda b: None,
            read=lambda: b'Z', disconnect=lambda: None,
        ),
    )
    return NetStation('127.0.0.1', 55513)


CONNECT_DRIFT_SETTINGS = [
    ('drift_min_samples', 21, '_drift_min_samples', 21),
    ('drift_min_span', 240.0, '_drift_min_span', 240.0),
    ('drift_max_delay', 0.02, '_drift_max_delay', 0.02),
    ('drift_max_residual', 0.01, '_drift_max_residual', 0.01),
    ('drift_window_minutes', 30.0, '_drift_window', 1800.0),
    ('drift_samples', 8, '_drift_samples_per_call', 8),
    ('drift_sample_spacing', 0.02, '_drift_sample_spacing', 0.02),
    ('drift_slew', 0.001, '_drift_slew', 0.001),
    ('drift_max_model_age', 300.0, '_drift_max_model_age', 300.0),
    ('auto_drift_interval', 45.0, '_auto_drift_interval', 45.0),
    ('auto_drift_min_pause', 0.75, '_auto_drift_min_pause', 0.75),
]


@pytest.mark.parametrize('kwarg,value,attr,expected', CONNECT_DRIFT_SETTINGS)
def test_each_connect_drift_argument_applies_on_its_own(
    kwarg, value, attr, expected, monkeypatch
):
    """Every drift setting must take effect when passed by itself.

    connect() used to gate each setter behind a condition naming only
    *some* of the arguments it forwarded, so passing one argument alone
    could be silently dropped because a sibling was absent. That shipped
    three separate times (auto_drift_background, the bare-connect() skip,
    and drift_max_residual). Passing each argument alone is the shape of
    test that catches the whole family at once.
    """
    ns = _bare_station(monkeypatch)
    ns.connect(ntp_ip='10.10.10.51', **{kwarg: value})
    try:
        assert getattr(ns, attr) == expected
    finally:
        ns._stop_auto_drift_thread()


def test_set_drift_requirements_leaves_omitted_settings_alone(
    tmp_path, monkeypatch
):
    """Omitting an argument must not reset it to a hardcoded default."""
    ns, _ = make_station(tmp_path, monkeypatch)
    ns.set_drift_requirements(min_samples=17, min_span=300.0)

    ns.set_drift_requirements(min_samples=21)      # retune one knob only
    assert ns._drift_min_samples == 21
    assert ns._drift_min_span == 300.0             # not reset to 90.0


def test_configure_auto_drift_does_not_silently_re_enable(
    tmp_path, monkeypatch
):
    """Retuning the interval must not turn sampling back on.

    `enabled` used to default to True and apply unconditionally, so an
    experiment that had disabled auto-drift and later called
    configure_auto_drift(interval=...) silently got the background thread
    restarted underneath it.
    """
    ns, _ = make_station(tmp_path, monkeypatch)
    ns.configure_auto_drift(enabled=False)
    assert ns._auto_drift_enabled is False

    ns.configure_auto_drift(interval=20.0)
    try:
        assert ns._auto_drift_enabled is False
        assert ns._auto_drift_interval == 20.0
        assert ns._auto_drift_thread is None
    finally:
        ns._stop_auto_drift_thread()


def test_send_event_raises_on_bad_start_instead_of_returning(
    tmp_path, monkeypatch
):
    """A bad `start` must raise, not be returned as a truthy object.

    send_event() used to `return TypeError(...)`, which is truthy, so the
    event was silently never enqueued and no except-clause could fire.
    """
    ns, _ = make_station(tmp_path, monkeypatch)
    ns._start_event_sender()
    try:
        with pytest.raises(TypeError):
            ns.send_event(start=object(), event_type='stm+')
        assert ns.pending_events() == 0
    finally:
        ns._stop_event_sender()


def test_gettime_never_refits_or_writes_to_disk(tmp_path, monkeypatch):
    """getTime() must be a pure reader: no refit, no I/O, ever.

    time_at_monotonic() holds _clock_lock. It used to reach
    _ntp_drift_regression() with a dirty model, which ran a full O(window)
    regression and, on a model transition, an _append_error_log() that does
    mkdir + open + write -- all inside the lock, on whatever thread called
    getTime(). In the documented usage that is a screen-flip callback.
    """
    ns, log = make_station(tmp_path, monkeypatch)
    ns.set_drift_requirements(min_samples=3, min_span=5.0)

    # Feed enough samples to engage the model, exactly as the background
    # sampler would. The refit must happen here, not on the next read.
    for index in range(6):
        elapsed = index * 15.0
        ns._record_ntp_drift_sample(
            types.SimpleNamespace(offset=1e-5 * elapsed, delay=0.002,
                                  tx_time=0.0),
            source='test',
            local_time=ns._sync_monotonic + elapsed,
            monotonic_time=ns._sync_monotonic + elapsed,
        )

    # The sampler leaves nothing for a reader to rebuild.
    assert ns._drift_model_dirty is False

    # Any refit or log write from here on is a bug.
    def explode(*args, **kwargs):
        raise AssertionError('getTime() did work it must never do')

    monkeypatch.setattr(ns, '_fit_ntp_drift_regression', explode)
    monkeypatch.setattr(ns, '_append_error_log', explode)
    for _ in range(50):
        ns.getTime()


def test_transition_records_are_written_outside_the_clock_lock(
    tmp_path, monkeypatch
):
    """The filesystem write must not happen while _clock_lock is held."""
    ns, log = make_station(tmp_path, monkeypatch)
    ns.set_drift_requirements(min_samples=3, min_span=5.0)

    seen = []
    real_append = ns._append_error_log

    def watched(record):
        # RLock.acquire(blocking=False) from the owning thread always
        # succeeds, so check the internal count instead: it is 0 only when
        # no one holds the lock.
        seen.append(ns._clock_lock._is_owned())
        return real_append(record)

    monkeypatch.setattr(ns, '_append_error_log', watched)

    for index in range(6):
        elapsed = index * 15.0
        ns._record_ntp_drift_sample(
            types.SimpleNamespace(offset=1e-5 * elapsed, delay=0.002,
                                  tx_time=0.0),
            source='test',
            local_time=ns._sync_monotonic + elapsed,
            monotonic_time=ns._sync_monotonic + elapsed,
        )
    ns._flush_pending_log_records()

    assert seen, 'expected at least one transition record'
    assert not any(seen), 'a log record was written while holding _clock_lock'


# --- Thread lifecycle ----------------------------------------------------

def test_concurrent_configure_auto_drift_starts_exactly_one_thread(
    tmp_path, monkeypatch
):
    """Racing configure_auto_drift() calls must not leak sampler threads.

    Start/stop used to read and write _auto_drift_thread with no lock, so
    two callers could both see None, both spawn a thread, and leave one
    running untracked -- still sampling, invisible to disconnect().
    """
    import threading as _threading

    ns, _ = make_station(tmp_path, monkeypatch)
    before = {t for t in _threading.enumerate()}

    barrier = _threading.Barrier(8)

    def racer():
        barrier.wait()
        ns.configure_auto_drift(enabled=True, interval=60.0, background=True)

    threads = [_threading.Thread(target=racer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    try:
        samplers = [
            t for t in _threading.enumerate()
            if t not in before and t.name == 'eci-drift-sampler'
        ]
        assert len(samplers) == 1, f'leaked sampler threads: {len(samplers)}'
    finally:
        ns._stop_auto_drift_thread()


def test_join_timeout_covers_a_full_burst_against_a_dead_server(
    tmp_path, monkeypatch
):
    """The join budget must exceed the worst-case burst duration.

    The NTP client defaults to a 5 s per-query timeout; with the default
    4-query burst that is ~20 s, against a join that used to be a flat 5 s. The
    thread was then abandoned still running, and a later start could add
    a second sampler alongside it.
    """
    ns, _ = make_station(tmp_path, monkeypatch)
    ns.set_drift_sampling(samples=4, spacing=0.05)

    worst_case = (
        4 * ns._ntp_timeout + 3 * ns._drift_sample_spacing
    )
    assert ns._auto_drift_join_timeout() > worst_case


def test_stalled_sampler_is_not_replaced_by_a_second_one(
    tmp_path, monkeypatch
):
    """If a sampler will not stop, refuse to start a duplicate."""
    import threading as _threading

    ns, _ = make_station(tmp_path, monkeypatch)
    wedged = _threading.Event()

    def wedge(*args, **kwargs):
        wedged.wait(timeout=10.0)

    ns._auto_drift_thread = _threading.Thread(target=wedge, daemon=True)
    ns._auto_drift_thread.start()
    original = ns._auto_drift_thread
    monkeypatch.setattr(ns, '_auto_drift_join_timeout', lambda: 0.05)
    try:
        ns._stop_auto_drift_thread()          # times out, thread still alive
        assert ns._auto_drift_thread is original, 'handle was dropped'

        ns._start_auto_drift_thread()         # must refuse
        assert ns._auto_drift_thread is original, 'started a duplicate'
    finally:
        wedged.set()
        original.join(timeout=5.0)


def test_each_sampler_thread_gets_its_own_stop_event(tmp_path, monkeypatch):
    """A new thread must not un-signal an older one via a shared Event."""
    ns, _ = make_station(tmp_path, monkeypatch)
    ns.configure_auto_drift(enabled=True, interval=60.0, background=True)
    first_event = ns._auto_drift_stop
    try:
        ns._stop_auto_drift_thread()
        assert first_event.is_set()

        ns._start_auto_drift_thread()
        # The old event stays set; the new thread watches a different one.
        assert first_event.is_set()
        assert ns._auto_drift_stop is not first_event
        assert not ns._auto_drift_stop.is_set()
    finally:
        ns._stop_auto_drift_thread()


# --- ECI response failures: recorded by default, raised on request -------

def test_garbled_response_does_not_end_a_recording(tmp_path, monkeypatch):
    """The default must survive a bad reply, not abort the run.

    _command()'s `strict` flag is documented to control whether parsing
    exceptions raise, but the InvalidECIResponse branch returned
    unconditionally -- so the one exception the flag names was the one it
    never reached. Honouring it uniformly would have made every event
    marker able to kill a recording, so the default is now non-raising.
    """
    ns, log = make_station(tmp_path, monkeypatch, reply=b'\x00')

    for index in range(3):
        result = ns.send_event(
            event_type='stm+', label=f'trial {index}', wait=True,
        )
        assert isinstance(result, dict)
        assert result['ok'] is False

    # The station is still fully usable.
    assert ns.getTime() is not None
    # And every failure is inspectable without parsing the log file.
    errors = ns.eci_errors()
    assert len(errors) == 3
    assert errors[-1]['cmd'] == 'EventData'
    assert errors[-1]['error'] == 'InvalidECIResponse'
    assert errors[-1]['label'] == 'trial 2'
    # Still written to the log too.
    assert len([r for r in read_records(log)
                if r['record'] == 'eci_response_error']) == 3


def test_strict_eci_raises_when_the_user_asks_for_it(tmp_path, monkeypatch):
    """Opting in must raise on the parsing exception, not just amp errors."""
    ns, _ = make_station(tmp_path, monkeypatch, reply=b'\x00')
    ns.set_strict_eci(True)

    with pytest.raises(netstation_module.InvalidECIResponse):
        ns.send_event(event_type='stm+', wait=True)


def test_strict_eci_is_off_by_default(tmp_path, monkeypatch):
    ns, _ = make_station(tmp_path, monkeypatch)
    assert ns._strict_eci is False


def test_eci_failures_surface_in_session_summary(tmp_path, monkeypatch):
    """A run with failed responses must not report itself as ok."""
    ns, _ = make_station(tmp_path, monkeypatch, reply=b'\x00')
    ns.send_event(event_type='stm+', wait=True)

    summary = ns.session_summary()
    assert summary['eci_response_failures'] == 1
    assert summary['ok'] is False


def test_eci_error_history_is_bounded(tmp_path, monkeypatch):
    """Keep recent failures handy without growing without bound."""
    ns, _ = make_station(tmp_path, monkeypatch, reply=b'\x00')
    ns._eci_errors_kept = 5

    for _ in range(12):
        ns.send_event(event_type='stm+', wait=True)

    assert len(ns.eci_errors()) == 5              # only the recent ones
    assert ns.session_summary()['eci_response_failures'] == 12   # all counted


# --- resync() deprecation ------------------------------------------------

def test_resync_raises_under_background_sampling(tmp_path, monkeypatch):
    """resync() is redundant and disruptive when the package self-samples.

    It forwards to sync_return_clock(), which can write real 'resy'
    markers into the recording and holds the ECI socket across several
    round trips. With background drift sampling running -- the default --
    the clock model is already current, so there is no upside.
    """
    ns, _ = make_station(tmp_path, monkeypatch)
    ns.configure_auto_drift(enabled=True, background=True)
    try:
        with pytest.raises(RuntimeError, match='sync_return_clock'):
            ns.resync()
    finally:
        ns._stop_auto_drift_thread()


def test_resync_warns_when_background_sampling_is_off(tmp_path, monkeypatch):
    """Without background sampling it still works, but is deprecated."""
    ns, _ = make_station(tmp_path, monkeypatch)
    ns.configure_auto_drift(enabled=True, background=False)
    ns._client_clock_start_ntp = 1.0        # precondition for the call

    with pytest.warns(DeprecationWarning, match='deprecated'):
        ns.resync()


def test_sync_return_clock_is_not_deprecated(tmp_path, monkeypatch):
    """The real method stays available with no warning and no error."""
    ns, _ = make_station(tmp_path, monkeypatch)
    ns._client_clock_start_ntp = 1.0

    with warnings.catch_warnings():
        warnings.simplefilter('error', DeprecationWarning)
        ns.sync_return_clock()
