from types import SimpleNamespace
import importlib
import json

import pytest

from egi_pynetstation.NetStation import NetStation


netstation_module = importlib.import_module('egi_pynetstation.NetStation')


def make_station():
    ns = NetStation('127.0.0.1', 55513)
    ns._connected = True
    ns._sync_monotonic = 1000.0
    ns._offset = 0.0
    ns._offset_mono = 0.0
    return ns


def add_drift_sample(ns, elapsed, offset, delay=0.001, sys_mono_skew=0.0):
    """Add one drift sample.

    The system and monotonic clock readings are kept consistent, so the
    monotonic-frame offset equals the raw offset unless a test explicitly
    asks for a skew between the two clocks.
    """
    response = SimpleNamespace(
        offset=offset,
        delay=delay,
        tx_time=0.0,
    )
    monotonic_time = ns._sync_monotonic + elapsed
    return ns._record_ntp_drift_sample(
        response,
        source='test',
        local_time=monotonic_time + sys_mono_skew,
        monotonic_time=monotonic_time,
    )


def test_default_drift_model_uses_rolling_window():
    ns = make_station()
    ns.set_drift_requirements(min_samples=2, min_span=1.0)

    for elapsed, offset in (
        (0.0, 0.000),
        (10.0, 0.010),
        (40.0, 0.040),
        (930.0, 0.930),
    ):
        add_drift_sample(ns, elapsed, offset)

    estimate = ns.drift_estimate()

    assert estimate['window'] == 900.0
    assert estimate['window_minutes'] == 15.0
    assert estimate['model_samples'] == 2
    assert estimate['model_span'] == 890.0
    assert abs(estimate['slope'] - 0.001) < 1e-12


def test_positive_drift_window_limits_model_samples():
    ns = make_station()
    ns.set_drift_requirements(min_samples=2, min_span=1.0)
    ns.set_drift_model_options(window_minutes=0.5)

    for elapsed, offset in (
        (0.0, 0.000),
        (10.0, 0.010),
        (20.0, 0.020),
        (40.0, 0.040),
    ):
        add_drift_sample(ns, elapsed, offset)

    estimate = ns.drift_estimate()

    assert estimate['window'] == 30.0
    assert estimate['window_minutes'] == 0.5
    assert estimate['model_samples'] == 3
    assert estimate['model_span'] == 30.0


def test_zero_drift_window_restores_all_sample_model():
    ns = make_station()
    ns.set_drift_requirements(min_samples=2, min_span=1.0)
    ns.set_drift_model_options(window_minutes=0.5)
    ns.set_drift_model_options(window_minutes=0)

    for elapsed, offset in (
        (0.0, 0.000),
        (10.0, 0.010),
        (20.0, 0.020),
        (40.0, 0.040),
    ):
        add_drift_sample(ns, elapsed, offset)

    estimate = ns.drift_estimate()

    assert estimate['window'] is None
    assert estimate['window_minutes'] is None
    assert estimate['model_samples'] == 4
    assert estimate['model_span'] == 40.0


def test_drift_model_updates_do_not_jump_predicted_offset():
    ns = make_station()
    ns.set_drift_requirements(min_samples=2, min_span=1.0)
    ns.set_drift_model_options(window_minutes=0)

    add_drift_sample(ns, 0.0, 0.000)
    add_drift_sample(ns, 10.0, 0.010)

    first = ns._predict_ntp_offset(20.0)

    add_drift_sample(ns, 20.0, 0.080)
    second = ns._predict_ntp_offset(20.0)

    assert abs(second - first) < 1e-12
    assert ns.drift_estimate()['active_slope'] is not None


def test_an_accepted_fit_is_active_before_the_next_event_timestamp():
    ns = make_station()
    ns.set_drift_requirements(min_samples=2, min_span=1.0)
    ns.set_drift_model_options(window_minutes=0)

    add_drift_sample(ns, 0.0, 0.000)
    add_drift_sample(ns, 10.0, 0.010)

    assert ns._drift_active_model is not None
    assert ns._drift_accepted_fits == 1
    assert ns._drift_active_model['slope'] == 0.001


def test_high_residual_drift_model_keeps_last_active_fit():
    ns = make_station()
    ns.set_drift_requirements(min_samples=2, min_span=1.0)
    ns.set_drift_model_options(max_residual=0.003, window_minutes=0)

    add_drift_sample(ns, 0.0, 0.000)
    add_drift_sample(ns, 10.0, 0.010)
    accepted = ns._predict_ntp_offset(20.0)

    add_drift_sample(ns, 20.0, 0.080)
    predicted = ns._predict_ntp_offset(20.0)
    rejected = ns.drift_estimate()

    assert rejected['slope'] is None
    assert rejected['active_slope'] is not None
    assert abs(predicted - accepted) < 1e-12


def test_drift_correction_converges_to_measured_offset_level():
    """The corrector must track the measured level, not integrate slope.

    The old model anchored each new fit on the previous model's output and
    discarded the fit's intercept, so the applied correction was an open
    loop and could random-walk away from the truth without bound. Here the
    true offset is a clean ramp; after enough samples the correction must
    sit on that ramp, not merely parallel to it.
    """
    ns = make_station()
    ns.set_drift_requirements(min_samples=3, min_span=10.0)
    ns.set_drift_model_options(max_residual=0.010, window_minutes=0)
    ns.set_drift_stability(slew=0.01)

    true_slope = 0.0001
    for step in range(60):
        elapsed = step * 10.0
        add_drift_sample(ns, elapsed, true_slope * elapsed)
        ns._predict_ntp_offset(elapsed)

    elapsed = 600.0
    predicted = ns._predict_ntp_offset(elapsed)
    expected = true_slope * elapsed

    assert abs(predicted - expected) < 1e-6


def test_level_error_is_retired_gradually_not_stepped():
    ns = make_station()
    ns.set_drift_requirements(min_samples=2, min_span=1.0)
    ns.set_drift_model_options(max_residual=0.100, window_minutes=0)
    ns.set_drift_stability(slew=0.001)

    add_drift_sample(ns, 0.0, 0.000)
    add_drift_sample(ns, 10.0, 0.010)
    before = ns._predict_ntp_offset(20.0)

    # A fit implying a very different level must not step the correction.
    add_drift_sample(ns, 20.0, 0.100)
    at_activation = ns._predict_ntp_offset(20.0)
    assert abs(at_activation - before) < 1e-12

    # ...but it must move toward that level as time passes. The movement is
    # the modeled slope plus at most `slew` seconds of retired level error
    # per second elapsed.
    slope = ns.drift_estimate()['active_slope']
    later = ns._predict_ntp_offset(21.0)
    retired = (later - at_activation) - slope * 1.0

    assert retired > 0
    assert retired <= 0.001 * 1.0 + 1e-12


def test_stale_model_stops_extrapolating_past_max_age():
    ns = make_station()
    ns.set_drift_requirements(min_samples=2, min_span=1.0)
    ns.set_drift_model_options(max_residual=0.010, window_minutes=0)
    ns.set_drift_stability(slew=0, max_model_age=100.0)

    add_drift_sample(ns, 0.0, 0.000)
    add_drift_sample(ns, 10.0, 0.010)
    ns._predict_ntp_offset(10.0)

    capped = ns._predict_ntp_offset(110.0)
    way_past = ns._predict_ntp_offset(10000.0)

    assert abs(way_past - capped) < 1e-12


def test_rejected_fits_are_counted_and_explained():
    ns = make_station()
    ns.set_drift_requirements(min_samples=2, min_span=1.0)
    ns.set_drift_model_options(max_residual=0.003, window_minutes=0)

    add_drift_sample(ns, 0.0, 0.000)
    add_drift_sample(ns, 10.0, 0.010)
    ns._predict_ntp_offset(10.0)
    assert ns._drift_last_reject_reason is None

    add_drift_sample(ns, 20.0, 0.080)
    ns._predict_ntp_offset(20.0)

    assert ns._drift_last_reject_reason == 'high_residual'
    assert ns._drift_rejected_fits >= 1


def test_os_clock_discipline_does_not_reach_event_timestamps():
    """An OS time daemon adjusting the system clock must not move timestamps.

    NTP reports offsets against the system clock, but event timestamps
    ride the monotonic clock. In the hour4 recording, macOS slewed the
    system clock by about -16 ms mid-run; because the drift model compared
    frames, that adjustment showed up as a bogus 30 ms/hour rate change and
    was injected straight into event timing. The true amplifier-versus-
    monotonic rate never changed.
    """
    true_slope = -4.7e-6              # about -17 ms/hour, as measured
    ns = make_station()
    ns.set_drift_requirements(min_samples=3, min_span=10.0)
    ns.set_drift_model_options(max_residual=0.003, window_minutes=0)

    def sys_clock_skew(elapsed):
        # System clock runs fast, then gets slewed back 16 ms at t=1200 s.
        skew = 9.8e-6 * elapsed
        return skew - 0.016 if elapsed >= 1200 else skew

    for step in range(240):
        elapsed = step * 15.0
        # Raw NTP offset is polluted by the system-clock skew; the true
        # amplifier-to-monotonic relationship is the clean ramp.
        raw = true_slope * elapsed - sys_clock_skew(elapsed)
        add_drift_sample(
            ns, elapsed, raw, sys_mono_skew=sys_clock_skew(elapsed)
        )

    estimate = ns.drift_estimate()

    # The recovered slope must be the true one, not the skew-contaminated
    # one, and the fit must be clean straight through the slew event.
    assert abs(estimate['slope'] - true_slope) < 1e-9
    assert estimate['model_max_residual'] < 1e-6
    assert ns._drift_last_reject_reason is None


def test_refresh_drift_model_forces_cached_model_to_refit(monkeypatch):
    ns = make_station()
    ns.set_drift_requirements(min_samples=2, min_span=1.0)
    ns.set_drift_model_options(max_residual=0.010, window_minutes=0)
    monkeypatch.setattr(netstation_module, 'ntp_monotonic_time',
                        lambda: 1020.0)

    add_drift_sample(ns, 0.0, 0.000)
    add_drift_sample(ns, 10.0, 0.010)
    first = ns.drift_estimate()
    assert abs(first['slope'] - 0.001) < 1e-12

    ns._drift_history[-1]['offset_mono'] = 0.020
    refreshed = ns.refresh_drift_model()

    assert abs(refreshed['slope'] - 0.002) < 1e-12
    assert abs(refreshed['active_slope'] - 0.002) < 1e-12


def test_presync_samples_are_kept_and_backfilled_at_sync():
    """Cap application is warm-up time the model used to discard.

    Samples taken before ntpsync() have no elapsed coordinate because the
    epoch does not exist yet. Their monotonic reading is in the same
    capture frame as the eventual anchor, so the coordinate is a pure
    translation -- and a translation of the origin leaves the slope
    unchanged.
    """
    ns = make_station()
    ns._sync_monotonic = None
    ns.set_drift_requirements(min_samples=2, min_span=1.0)
    ns.set_drift_model_options(window_minutes=0)

    # 30 s of sampling before the epoch exists.
    for monotonic_time, offset in (
        (1000.0, 0.000), (1010.0, 0.010),
        (1020.0, 0.020), (1030.0, 0.030),
    ):
        ns._record_ntp_drift_sample(
            SimpleNamespace(offset=offset, delay=0.001, tx_time=0.0),
            source='test',
            local_time=monotonic_time,
            monotonic_time=monotonic_time,
        )

    assert ns.drift_estimate()['slope'] is None
    assert all(s['elapsed'] is None for s in ns._drift_history)

    # ntpsync() lands here.
    ns._sync_monotonic = 1030.0
    assert ns._backfill_presync_elapsed() == 4
    ns._drift_model_dirty = True

    estimate = ns.drift_estimate()
    assert estimate['slope'] == pytest.approx(0.001)
    # Pre-sync samples sit at negative elapsed; that is the point.
    assert [s['elapsed'] for s in ns._drift_history] == [
        -30.0, -20.0, -10.0, 0.0
    ]


def test_backfilled_slope_matches_the_same_samples_taken_after_sync():
    """The origin shift must not move the slope, only the intercept."""
    before = make_station()
    before._sync_monotonic = None
    after = make_station()
    for station in (before, after):
        station.set_drift_requirements(min_samples=2, min_span=1.0)
        station.set_drift_model_options(window_minutes=0)

    for index, offset in enumerate((0.000, 0.010, 0.020)):
        monotonic_time = 1000.0 + index * 10.0
        before._record_ntp_drift_sample(
            SimpleNamespace(offset=offset, delay=0.001, tx_time=0.0),
            source='test',
            local_time=monotonic_time, monotonic_time=monotonic_time,
        )
        add_drift_sample(after, index * 10.0, offset)

    before._sync_monotonic = 1020.0
    before._backfill_presync_elapsed()
    before._drift_model_dirty = True

    assert (before.drift_estimate()['slope']
            == pytest.approx(after.drift_estimate()['slope']))


def test_backfill_leaves_already_coordinated_samples_alone():
    ns = make_station()
    ns.set_drift_requirements(min_samples=2, min_span=1.0)
    add_drift_sample(ns, 0.0, 0.000)
    add_drift_sample(ns, 10.0, 0.010)

    assert ns._backfill_presync_elapsed() == 0
    assert [s['elapsed'] for s in ns._drift_history] == [0.0, 10.0]


def test_presync_samples_do_not_accumulate_rejected_fits():
    """A prep window must not open the recording with a log full of noise.

    Every sample used to trigger a refit. Pre-sync samples cannot enter a
    fit, so each one scored a 'too_few_samples' rejection -- eighty of
    them across a twenty-minute cap application, with nothing wrong.
    """
    ns = make_station()
    ns._sync_monotonic = None

    for index in range(80):
        monotonic_time = 1000.0 + index * 15.0
        ns._record_ntp_drift_sample(
            SimpleNamespace(offset=0.0, delay=0.001, tx_time=0.0),
            source='test',
            local_time=monotonic_time, monotonic_time=monotonic_time,
        )

    assert len(ns._drift_history) == 80
    assert ns._drift_rejected_fits == 0
    assert ns._drift_last_reject_reason is None


def test_warmup_model_engages_then_stable_model_takes_over():
    ns = make_station()
    ns.set_drift_requirements(min_samples=7, min_span=60.0)
    ns.set_drift_warmup(
        enabled=True, min_samples=3, min_span=10.0, interval=2.0,
    )
    ns.set_drift_model_options(window_minutes=0)

    for elapsed in (0.0, 5.0, 10.0):
        add_drift_sample(ns, elapsed, elapsed * 0.0001)

    warmup = ns.drift_estimate()
    assert warmup['model_stage'] == 'warmup'
    assert warmup['active_stage'] == 'warmup'
    assert warmup['stable_engaged'] is False
    assert ns._effective_drift_interval() == 2.0

    for elapsed in (20.0, 30.0, 45.0, 60.0):
        add_drift_sample(ns, elapsed, elapsed * 0.0001)

    stable = ns.drift_estimate()
    assert stable['model_stage'] == 'stable'
    assert stable['active_stage'] == 'stable'
    assert stable['stable_engaged'] is True
    assert ns._effective_drift_interval() == ns._auto_drift_interval


def test_setting_a_warmup_option_enables_staged_fitting():
    ns = make_station()
    result = ns.set_drift_warmup(min_span=12.0)

    assert result['enabled'] is True
    assert result['min_span'] == 12.0


def test_stable_model_never_downgrades_to_warmup():
    ns = make_station()
    ns.set_drift_requirements(min_samples=4, min_span=30.0)
    ns.set_drift_warmup(enabled=True, min_samples=2, min_span=5.0)
    ns.set_drift_model_options(window_minutes=0)
    for elapsed in (0.0, 10.0, 20.0, 30.0):
        add_drift_sample(ns, elapsed, elapsed * 0.0001)
    assert ns.drift_estimate()['active_stage'] == 'stable'

    # Changing the stable gates makes the retained evidence insufficient.
    # The last stable model must remain active; the warmup gate is no longer
    # eligible once stable correction has engaged.
    ns.set_drift_requirements(min_samples=20, min_span=300.0)
    report = ns.refresh_drift_model()
    assert report['model_stage'] is None
    assert report['active_stage'] == 'stable'


def test_rebase_translates_history_and_reanchors_without_a_step():
    ns = make_station()
    ns.set_drift_requirements(min_samples=2, min_span=10.0)
    ns.set_drift_model_options(window_minutes=0)
    add_drift_sample(ns, 0.0, 0.000)
    add_drift_sample(ns, 10.0, 0.010)
    assert ns._drift_active_model is not None

    ns._recording_count = 2
    ns._rebase_drift_epoch(1025.0, 0.025)
    ns._sync_monotonic = 1025.0
    ns._offset_mono = 0.025

    assert [sample['elapsed'] for sample in ns.drift_history()] == [
        -25.0, -15.0,
    ]
    assert ns._predict_active_ntp_offset(0.0) == pytest.approx(0.025)
    assert ns._drift_active_model['anchor_elapsed'] == 0.0


# --- readiness -----------------------------------------------------------

def ready_station(pending=0.0, model_age=0.0, slew=0.0002):
    """A station with an active model whose level error is `pending`."""
    ns = make_station()
    ns._syncepoch = 0.0
    ns._ntp_last_success_monotonic = netstation_module.time.monotonic()
    ns._sync_monotonic = netstation_module.ntp_monotonic_time() - model_age
    ns._drift_active_model = {
        'model_id': ('x',),
        'slope': -155e-3 / 3600.0,
        'anchor_elapsed': 0.0,
        'anchor_offset': 0.0,
        'raw_anchor_offset': pending,
        'error': pending,
        'slew': slew,
        'activated_local_time': 0.0,
    }
    ns._drift_accepted_fits = 1
    return ns


def test_readiness_reports_disabled_before_anything_else():
    ns = ready_station()
    ns.set_drift_correction(False)
    assert ns.drift_ready()['reason'] == 'disabled'


def test_readiness_reports_not_synced_before_the_clock_epoch_exists():
    ns = ready_station()
    ns._syncepoch = None
    assert ns.drift_ready()['reason'] == 'not_synced'


def test_readiness_reports_warming_up_with_no_model():
    ns = make_station()
    ns._syncepoch = 0.0
    verdict = ns.drift_ready()
    assert verdict['ready'] is False
    assert verdict['reason'] == 'warming_up'
    # Nothing collected yet: the full count gate at the sampling interval.
    assert verdict['estimated_seconds_remaining'] == pytest.approx(
        ns.drift_settings()['drift_min_samples']
        * ns.drift_settings()['auto_drift_interval']
    )


def test_readiness_forecasts_whichever_gate_is_further_away():
    """Both gates must be satisfied, and they clear at different times."""
    ns = make_station()
    ns._syncepoch = 0.0
    ns.set_drift_requirements(min_samples=4, min_span=300.0)
    ns.configure_auto_drift(interval=10.0)
    # Four samples already, but only 30 s of span: the span gate is later.
    for index in range(4):
        add_drift_sample(ns, index * 10.0, 0.0)
    verdict = ns.drift_ready()
    assert verdict['reason'] == 'warming_up'
    assert verdict['estimated_seconds_remaining'] == pytest.approx(270.0)


def test_readiness_reports_settling_while_the_slew_still_owes_correction():
    """The interval session_summary() calls healthy but timestamps are not.

    A fit has been accepted, so 'ok' is True, but several milliseconds of
    level error have not been applied yet.
    """
    ns = ready_station(pending=0.0072, model_age=0.0)
    verdict = ns.drift_ready()
    assert verdict['ready'] is False
    assert verdict['reason'] == 'settling'
    assert verdict['pending_correction_ms'] == pytest.approx(7.2)
    # 7.2 ms less the 1 ms tolerance, retired at 0.2 ms/s.
    assert verdict['estimated_seconds_remaining'] == pytest.approx(31.0)
    assert ns.session_summary()['ok'] is True


def test_readiness_clears_once_the_slew_has_retired_the_error():
    ns = ready_station(pending=0.0072, model_age=40.0)
    verdict = ns.drift_ready()
    assert verdict['pending_correction_ms'] == pytest.approx(0.0)
    assert verdict['ready'] is True
    assert verdict['reason'] is None


def test_readiness_honours_a_caller_supplied_pending_tolerance():
    ns = ready_station(pending=0.0072, model_age=0.0)
    assert ns.drift_ready(max_pending=0.010)['ready'] is True
    assert ns.drift_ready(max_pending=0.001)['reason'] == 'settling'


def test_readiness_reports_a_stalled_model():
    ns = ready_station()
    ns._drift_stalled = True
    ns._drift_last_reject_reason = 'high_residual'
    verdict = ns.drift_ready()
    assert verdict['reason'] == 'stalled'
    assert verdict['last_reject_reason'] == 'high_residual'


def test_readiness_reports_stalled_before_settling():
    ns = ready_station(pending=0.0072, model_age=0.0)
    ns._drift_stalled = True
    assert ns.drift_ready()['reason'] == 'stalled'


def test_readiness_reports_a_model_older_than_extrapolation_allows():
    ns = ready_station(model_age=900.0)
    ns.set_drift_stability(max_model_age=600.0)
    assert ns.drift_ready()['reason'] == 'model_expired'


def test_readiness_reports_expired_model_before_settling():
    ns = ready_station(pending=0.0072, model_age=900.0)
    ns.set_drift_stability(max_model_age=600.0)
    assert ns.drift_ready()['reason'] == 'model_expired'


def test_readiness_reports_stale_sampling():
    ns = ready_station()
    ns._ntp_last_success_monotonic = (
        netstation_module.time.monotonic() - 10_000.0
    )
    assert ns.drift_ready()['reason'] == 'sampling_expired'


def test_wait_returns_at_once_when_already_ready():
    ns = ready_station()
    verdict = ns.wait_for_drift(timeout=30.0, poll=5.0)
    assert verdict['ready'] is True
    assert verdict['timed_out'] is False


def test_wait_times_out_without_raising_and_says_what_it_saw():
    """Starting uncorrected is a legitimate choice, not an error."""
    ns = ready_station(pending=1.0)
    verdict = ns.wait_for_drift(timeout=0.0, poll=0.1)
    assert verdict['timed_out'] is True
    assert verdict['ready'] is False
    assert verdict['reason'] == 'settling'


def test_wait_gives_the_callback_what_a_countdown_needs():
    ns = ready_station(pending=1.0)
    seen = []
    ns.wait_for_drift(timeout=0.3, poll=0.1, on_wait=seen.append)
    assert len(seen) >= 2
    for verdict in seen:
        assert 'seconds_waited' in verdict
        assert 'seconds_remaining' in verdict
        assert verdict['reason'] == 'settling'
    assert seen[-1]['seconds_waited'] >= seen[0]['seconds_waited']
    assert seen[-1]['seconds_remaining'] <= seen[0]['seconds_remaining']


def test_wait_can_be_cancelled_by_raising_from_the_callback():
    ns = ready_station(pending=1.0)

    def cancel(verdict):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        ns.wait_for_drift(timeout=30.0, poll=0.1, on_wait=cancel)


def test_wait_rejects_nonsense_arguments():
    ns = ready_station()
    with pytest.raises(ValueError):
        ns.wait_for_drift(timeout=-1.0)
    with pytest.raises(ValueError):
        ns.wait_for_drift(poll=0.0)


def _logged_station(tmp_path):
    """A station whose queued transition records land in a readable log."""
    ns = make_station()
    ns._error_log = str(tmp_path / 'drift.jsonl')
    ns.set_drift_requirements(min_samples=2, min_span=1.0)
    ns.set_drift_model_options(window_minutes=0)
    return ns


def _records(ns, record=None):
    ns._flush_pending_log_records()
    with open(ns._error_log, encoding='utf-8') as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    if record is not None:
        rows = [row for row in rows if row.get('record') == record]
    return rows


def test_multi_second_spike_is_rejected_before_it_enters_the_fit(tmp_path):
    ns = _logged_station(tmp_path)
    # A clean, slowly drifting baseline engages the model.
    for elapsed, offset in ((0.0, 0.000), (10.0, 0.010), (20.0, 0.020)):
        add_drift_sample(ns, elapsed, offset)
    slope_before = ns.drift_estimate()['slope']

    # A three-second glitch three seconds later: the slope says ~0.023,
    # this reads 3.0. It must not touch the fit.
    add_drift_sample(ns, 23.0, 3.000)

    assert ns._drift_history[-1]['valid'] is False
    assert ns._drift_history[-1]['reject_reason'] == 'level_outlier'
    assert ns.drift_estimate()['slope'] == pytest.approx(slope_before)
    rejected = _records(ns, 'drift_sample_rejected')
    assert rejected and rejected[-1]['reject_reason'] == 'level_outlier'


def test_honestly_drifted_sample_after_a_long_gap_is_not_an_outlier(tmp_path):
    ns = _logged_station(tmp_path)
    for elapsed, offset in ((0.0, 0.000), (10.0, 0.010), (20.0, 0.020)):
        add_drift_sample(ns, elapsed, offset)
    # 800 s later the offset has honestly drifted by 0.800 s at the same
    # 0.001 s/s slope. The max-age clamp must not make this look like a
    # glitch.
    add_drift_sample(ns, 820.0, 0.820)
    assert ns._drift_history[-1]['valid'] is True
    assert not _records(ns, 'drift_sample_rejected')


def test_level_excursion_opens_and_closes_one_record_each(tmp_path):
    ns = _logged_station(tmp_path)
    ns.set_drift_stability(slew=0.0)  # retire level error instantly
    ns.set_drift_monitoring(excursion_threshold=0.005)
    for elapsed, offset in ((0.0, 0.000), (10.0, 0.010), (20.0, 0.020)):
        add_drift_sample(ns, elapsed, offset)

    # A sample 0.030 s above where the model predicts: an excursion, but
    # well under the 0.1 s outlier bound so it is a valid sample.
    add_drift_sample(ns, 30.0, 0.060)
    assert ns._drift_history[-1]['valid'] is True
    assert len(_records(ns, 'drift_level_excursion')) == 1
    assert not _records(ns, 'drift_level_recovered')

    # Back onto the model's line: the excursion closes exactly once.
    add_drift_sample(ns, 40.0, 0.040)
    add_drift_sample(ns, 50.0, 0.050)
    recovered = _records(ns, 'drift_level_recovered')
    assert len(recovered) == 1
    assert recovered[0]['peak_level_error'] == pytest.approx(0.030, abs=5e-3)


def test_status_heartbeat_is_emitted_on_its_interval(tmp_path):
    ns = _logged_station(tmp_path)
    ns.set_drift_monitoring(status_interval=100.0)
    add_drift_sample(ns, 0.0, 0.000)
    add_drift_sample(ns, 10.0, 0.010)  # model engages here
    # First engaged sample seeds the heartbeat clock; nothing due yet.
    add_drift_sample(ns, 50.0, 0.050)
    assert not _records(ns, 'drift_model_status')
    # Past the interval from the seed.
    add_drift_sample(ns, 160.0, 0.160)
    status = _records(ns, 'drift_model_status')
    assert len(status) == 1
    assert status[0]['model_stage'] is not None
    assert 'outstanding_level_error_ms' in status[0]


def test_status_heartbeat_interval_survives_epoch_rebase(tmp_path):
    """A second recording must not inherit the first epoch's elapsed clock."""
    ns = _logged_station(tmp_path)
    ns.set_drift_monitoring(status_interval=100.0)
    add_drift_sample(ns, 0.0, 0.000)
    add_drift_sample(ns, 10.0, 0.010)  # engage and seed at elapsed=10
    add_drift_sample(ns, 120.0, 0.120)  # first heartbeat
    assert len(_records(ns, 'drift_model_status')) == 1

    # Recording 2 starts 200 seconds after recording 1. Every retained
    # elapsed coordinate, including the heartbeat clock, moves back 200 s.
    ns._recording_count = 2
    ns._rebase_drift_epoch(1200.0, 0.200)
    ns._sync_monotonic = 1200.0
    ns._offset_mono = 0.200
    assert ns._drift_status_last_elapsed == pytest.approx(-80.0)

    # 110 seconds have passed since the prior heartbeat in the translated
    # coordinate system, so recording 2 must emit on its ordinary schedule.
    add_drift_sample(ns, 30.0, 0.230)
    assert len(_records(ns, 'drift_model_status')) == 2


def test_disabling_the_outlier_bound_lets_a_spike_through(tmp_path):
    ns = _logged_station(tmp_path)
    ns.set_drift_monitoring(sample_reject_offset=0)
    for elapsed, offset in ((0.0, 0.000), (10.0, 0.010), (20.0, 0.020)):
        add_drift_sample(ns, elapsed, offset)
    add_drift_sample(ns, 23.0, 3.000)
    assert ns._drift_history[-1]['valid'] is True
    assert not _records(ns, 'drift_sample_rejected')
