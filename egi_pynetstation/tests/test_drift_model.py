from types import SimpleNamespace
import importlib

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
