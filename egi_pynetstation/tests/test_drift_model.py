from types import SimpleNamespace

from egi_pynetstation.NetStation import NetStation


def make_station():
    ns = NetStation('127.0.0.1', 55513)
    ns._connected = True
    ns._sync_monotonic = 1000.0
    ns._offset = 0.0
    return ns


def add_drift_sample(ns, elapsed, offset, delay=0.001):
    response = SimpleNamespace(
        offset=offset,
        delay=delay,
        tx_time=0.0,
    )
    return ns._record_ntp_drift_sample(
        response,
        source='test',
        local_time=elapsed,
        monotonic_time=ns._sync_monotonic + elapsed,
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
