"""Tests for the JSON-lines ECI error log.

The log exists for post-hoc diagnosis, so the things it must not do are:
lose the failure an experiment is most likely to hit, omit the context
needed to explain it, or become a second failure itself.
"""

import importlib
import json
import time
import types


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
    ns._sync_monotonic = time.monotonic()
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
    assert engaged['elapsed'] == 60.0
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

    configure_auto_drift() only records intent. Without background=True,
    sample_drift_if_due() is the only thing that takes a sample, so an
    experiment that never calls it loses drift correction entirely and
    nothing else says so.
    """
    ns, log = make_station(tmp_path, monkeypatch)
    ns.configure_auto_drift(enabled=True, interval=0.01)   # foreground
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
    )
    try:
        assert ns._auto_drift_enabled is True
        assert ns._auto_drift_interval == 15.0
        assert ns._auto_drift_min_pause == 0.35
        assert ns._auto_drift_background is False
    finally:
        ns._stop_auto_drift_thread()


def test_connect_leaves_auto_drift_off_unless_asked(tmp_path, monkeypatch):
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
    assert ns._auto_drift_enabled is False
    assert ns._auto_drift_thread is None


def test_removed_legacy_alias_is_gone():
    """2.0 drops resync_do_not_use_not_recommended()."""
    assert not hasattr(NetStation, 'resync_do_not_use_not_recommended')
    assert hasattr(NetStation, 'resync')
