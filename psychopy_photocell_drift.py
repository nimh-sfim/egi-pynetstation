#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""PsychoPy photocell timing test for EGI NetStation drift correction.

Shows a black screen with a white dot. Every dot onset calls
``ns.send_event()`` directly from the PsychoPy flip callback, relying on the
package's built-in asynchronous sender to keep the network write off the
critical path.

This script deliberately uses the package's own threading rather than
managing a worker itself, so that a run validates what real experiments
will actually do. The key metric is ``send_call_span_ms``: how long
``send_event()` blocks the flip callback. Run with ``--sync-events`` to
measure the same thing with the sender disabled, for comparison.
"""

import csv
import statistics
import sys
import time
from argparse import ArgumentParser
from pathlib import Path

from egi_pynetstation.NetStation import NetStation


def connect_with_drift_options(ns: NetStation, ntp_ip: str, args) -> None:
    """Connect using this repository's drift and async-sender options."""
    try:
        ns.connect(
            ntp_ip=ntp_ip,
            handshake=True,
            async_events=not args.sync_events,
            drift_correction=not args.no_drift_correction,
            drift_min_samples=args.drift_min_samples,
            drift_min_span=args.drift_min_span,
            drift_max_delay=args.drift_max_delay,
            drift_max_residual=args.drift_max_residual,
            drift_window_minutes=args.drift_window_minutes,
            drift_samples=args.drift_samples,
            drift_sample_spacing=args.drift_sample_spacing,
            drift_slew=args.drift_slew,
            drift_max_model_age=args.drift_max_model_age,
        )
    except TypeError as err:
        # Fail loudly rather than silently validating different code. The
        # usual cause is an older copy of the package shadowing this
        # repository; see the installation notes in README.md.
        raise SystemExit(
            'The imported egi_pynetstation does not support the options this '
            'test requires, so the run would not measure what it claims to.\n'
            f'  error: {err}\n'
            f'  loaded from: {NetStation.__module__}\n'
            'Install this repository with `pip install -e .` and remove any '
            'older copy with `pip uninstall egi_pynetstation`.'
        )


def build_isi_sequence(duration: float) -> list:
    """Build mixed inter-stimulus intervals for a roughly five-minute run."""
    isis = []
    elapsed = 0.0

    def add(value: float) -> None:
        nonlocal elapsed
        if elapsed + value < duration:
            isis.append(value)
            elapsed += value

    add(2.0)

    for _ in range(10):
        add(1.0)
    for _ in range(20):
        add(0.5)
    for _ in range(15):
        add(2.0)
    for _ in range(6):
        add(10.0)

    for value in ([1.0, 3.0, 2.0] * 5):
        add(value)

    for _ in range(20):
        add(0.5)

    while elapsed + 3.0 < duration - 1.0:
        add(3.0)

    return isis


def add_clock_diagnostics(record: dict, ns: NetStation) -> None:
    """Add flat timing/correction diagnostics to a CSV record.

    Never call this from a flip callback; it builds a full state snapshot.
    """
    package_time = record.get('package_time')
    psychopy_time = record.get('psychopy_time')
    if isinstance(package_time, (int, float)) and isinstance(
        psychopy_time, (int, float)
    ):
        record['package_minus_psychopy_ms'] = (
            package_time - psychopy_time
        ) * 1000.0

    try:
        state = ns.clock_state()
    except Exception as err:
        record['clock_state_error'] = f'{type(err).__name__}: {err}'
        return

    initial = state.get('ntp_offset')
    predicted = state.get('predicted_ntp_offset')
    if isinstance(initial, (int, float)) and isinstance(predicted, (int, float)):
        record['drift_correction_ms'] = (predicted - initial) * 1000.0

    slope = state.get('drift_slope')
    if isinstance(slope, (int, float)):
        record['drift_slope_ms_per_hour'] = slope * 1000.0 * 3600.0
    active_slope = state.get('active_drift_slope')
    if isinstance(active_slope, (int, float)):
        record['active_drift_slope_ms_per_hour'] = (
            active_slope * 1000.0 * 3600.0
        )

    for source, target in (
        ('drift_samples', 'drift_samples'),
        ('drift_valid_samples', 'drift_valid_samples'),
        ('drift_rejected_samples', 'drift_rejected_samples'),
        ('drift_model_samples', 'drift_model_samples'),
        ('drift_model_span', 'drift_model_span_s'),
        ('drift_model_max_residual', 'drift_model_max_residual_ms'),
        ('drift_model_rms_residual', 'drift_model_rms_residual_ms'),
        ('drift_max_residual', 'drift_max_residual_ms'),
        ('drift_window', 'drift_window_s'),
        ('drift_accepted_fits', 'drift_accepted_fits'),
        ('drift_rejected_fits', 'drift_rejected_fits'),
        ('drift_last_reject_reason', 'drift_last_reject_reason'),
        ('drift_pending_error', 'drift_pending_error_ms'),
        ('drift_model_age', 'drift_model_age_s'),
        ('drift_samples_per_call', 'drift_samples_per_call'),
        ('sys_mono_skew', 'sys_mono_skew_ms'),
        ('ntp_offset_raw', 'ntp_offset_raw'),
    ):
        value = state.get(source)
        if value is None:
            continue
        if target.endswith('_ms') and isinstance(value, (int, float)):
            value *= 1000.0
        record[target] = value


# Keys that abort the run. 'Q' is included alongside 'q' because some
# PsychoPy keyboard backends report a shifted key by its uppercase name.
QUIT_KEYS = ['escape', 'q', 'Q']


CSV_COLUMNS = [
    'trial',
    'phase',
    'send_mode',
    'planned_onset',
    'psychopy_time',
    'package_time',
    'package_minus_psychopy_ms',
    'flip_local_time',
    'flip_monotonic_time',
    # How long send_event() blocked the flip callback. This is the metric
    # that validates the package's asynchronous sender.
    'send_call_span_ms',
    'pending_events',
    'send_result',
    'send_error',
    'drift_correction_ms',
    'drift_slope_ms_per_hour',
    'active_drift_slope_ms_per_hour',
    'drift_samples',
    'drift_valid_samples',
    'drift_rejected_samples',
    'drift_model_samples',
    'drift_model_span_s',
    'drift_model_max_residual_ms',
    'drift_model_rms_residual_ms',
    'drift_max_residual_ms',
    'drift_window_s',
    'drift_accepted_fits',
    'drift_rejected_fits',
    'drift_last_reject_reason',
    'drift_pending_error_ms',
    'drift_model_age_s',
    'drift_samples_per_call',
    'sys_mono_skew_ms',
    'ntp_offset_raw',
    'clock_state_error',
    'ntp_offset',
    'ntp_delay',
    'ntp_valid',
    'ntp_reject_reason',
    'ntp_burst_size',
    'ntp_burst_ok',
    'ntp_burst_worst_delay',
    'ntp_offset_mono',
    'ntp_sample_sys_mono_skew',
    'sync_before_stimulus',
    'sync_after_stimulus',
    'sync_local_time',
    'sync_result',
    'sync_error',
    'post_sync_local_time',
    'post_sync_result',
    'post_sync_error',
]


def write_records(path: str, records: list) -> None:
    if not path:
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(
            csvfile, fieldnames=CSV_COLUMNS, extrasaction='ignore'
        )
        writer.writeheader()
        writer.writerows(records)


def summarize_send_timing(records: list, send_mode: str) -> None:
    """Report how long send_event() held the flip callback."""
    spans = [
        r['send_call_span_ms'] for r in records
        if isinstance(r.get('send_call_span_ms'), (int, float))
    ]
    if not spans:
        return
    spans_sorted = sorted(spans)
    pending = [
        r['pending_events'] for r in records
        if isinstance(r.get('pending_events'), (int, float))
    ]
    print(f'\nsend_event() call span in the flip callback ({send_mode} mode):')
    print(f'  n           {len(spans)}')
    print(f'  mean        {statistics.mean(spans) * 1000:8.1f} us')
    print(f'  median      {statistics.median(spans) * 1000:8.1f} us')
    print(f'  p95         {spans_sorted[int(0.95 * len(spans))] * 1000:8.1f} us')
    print(f'  max         {max(spans) * 1000:8.1f} us')
    if pending:
        print(f'  queue depth max {max(pending)} (0 means the sender kept up)')


def resolve_network(args):
    if args.mode == 'local':
        return (
            args.ip_cmd or '127.0.0.1',
            args.ip_clock or '216.239.35.4',
            args.port or 9885,
        )
    if args.mode == 'amp':
        return (
            args.ip_cmd or '10.10.10.42',
            args.ip_clock or '10.10.10.51',
            args.port or 55513,
        )
    if not (args.ip_cmd and args.ip_clock and args.port):
        raise ValueError('custom mode requires --ip-cmd, --ip-clock, and --port')
    return args.ip_cmd, args.ip_clock, args.port


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description='Run a PsychoPy white-dot photocell drift test.'
    )
    parser.add_argument('mode', choices=['local', 'amp', 'custom'])
    parser.add_argument('--ip-cmd', help='Net Station / command IPv4 address')
    parser.add_argument('--ip-clock', help='NTP server / amplifier IPv4 address')
    parser.add_argument('--port', type=int, help='ECI TCP port')
    parser.add_argument('--duration', type=float, default=300.0)
    parser.add_argument('--dot-duration', type=float, default=0.100)
    parser.add_argument('--dot-radius', type=float, default=0.045)
    parser.add_argument('--dot-pos', type=float, nargs=2, default=(0.72, 0.40))
    parser.add_argument('--sample-interval', type=float, default=15.0)
    parser.add_argument('--drift-min-samples', type=int, default=13)
    parser.add_argument('--drift-min-span', type=float, default=180.0)
    parser.add_argument(
        '--drift-max-delay',
        type=float,
        default=0.010,
        help='Reject NTP drift samples above this round-trip delay, in seconds',
    )
    parser.add_argument(
        '--drift-max-residual',
        type=float,
        default=0.003,
        help=(
            'Reject NTP drift fits whose maximum absolute residual exceeds '
            'this many seconds'
        ),
    )
    parser.add_argument(
        '--drift-window-minutes',
        type=float,
        default=15.0,
        help=(
            'Use only the last N minutes of valid drift samples for the model; '
            '0 uses all valid samples'
        ),
    )
    parser.add_argument(
        '--drift-samples',
        type=int,
        default=4,
        help=(
            'NTP queries per drift sample; the lowest-delay reply is kept. '
            'Higher values reduce offset noise at the cost of a longer pause'
        ),
    )
    parser.add_argument(
        '--drift-sample-spacing',
        type=float,
        default=0.05,
        help='Seconds between NTP queries within one drift sample burst',
    )
    parser.add_argument(
        '--drift-min-pause',
        type=float,
        default=0.35,
        help=(
            'Minimum idle time, in seconds, required between stimuli before '
            'an NTP drift burst is taken; shorter gaps are skipped'
        ),
    )
    parser.add_argument(
        '--drift-slew',
        type=float,
        default=0.0002,
        help=(
            'Maximum rate, in seconds of correction per second elapsed, at '
            'which level errors are retired; 0 applies them instantly'
        ),
    )
    parser.add_argument(
        '--drift-max-model-age',
        type=float,
        default=600.0,
        help=(
            'Stop extrapolating a fitted slope after this many seconds; '
            '0 extrapolates without bound'
        ),
    )
    parser.add_argument(
        '--ntpsync-every',
        type=int,
        default=0,
        help=(
            'Diagnostic only: send ECI ntpsync before every Nth dot. '
            'Use 0 to disable repeated ECI clock syncs.'
        ),
    )
    parser.add_argument(
        '--ntpsync-after-every',
        type=int,
        default=0,
        help=(
            'Diagnostic only: send ECI ntpsync after every Nth dot. '
            'Use 0 to disable repeated post-stimulus ECI clock syncs.'
        ),
    )
    parser.add_argument(
        '--sync-events',
        action='store_true',
        help=(
            'Disable the package asynchronous sender so send_event() writes '
            'to the socket inline. Use to measure what the sender is worth'
        ),
    )
    parser.add_argument(
        '--no-drift-correction',
        action='store_true',
        help='Disable client-side NTP drift correction',
    )
    parser.add_argument('--fullscreen', action='store_true')
    parser.add_argument('--screen', type=int, default=0)
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--error-log', help='JSON-lines ECI error log path')
    parser.add_argument('--log', help='CSV file for PsychoPy/ECI event timing')
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.ntpsync_every < 0:
        parser.error('--ntpsync-every must be >= 0')
    if args.ntpsync_after_every < 0:
        parser.error('--ntpsync-after-every must be >= 0')
    if args.drift_max_delay <= 0:
        parser.error('--drift-max-delay must be positive')
    if args.drift_max_residual <= 0:
        parser.error('--drift-max-residual must be positive')
    if args.drift_window_minutes < 0:
        parser.error('--drift-window-minutes must be non-negative')
    if args.drift_samples < 1:
        parser.error('--drift-samples must be at least 1')
    if args.drift_sample_spacing < 0:
        parser.error('--drift-sample-spacing must be non-negative')
    if args.drift_slew < 0:
        parser.error('--drift-slew must be non-negative')
    if args.drift_max_model_age < 0:
        parser.error('--drift-max-model-age must be non-negative')
    if args.drift_min_pause < 0:
        parser.error('--drift-min-pause must be non-negative')

    ip_cmd, ip_clock, port = resolve_network(args)
    send_mode = 'sync' if args.sync_events else 'async'
    records = []
    ns = None
    win = None

    try:
        from psychopy import core, event, visual

        ns = NetStation(
            ip_cmd,
            port,
            debug=args.debug,
            error_log=args.error_log,
        )
        connect_with_drift_options(ns, ip_clock, args)
        ns.send_command('BeginRecording')
        ns.ntpsync()

        # The package owns the drift-sampling schedule; this script owns the
        # timing-safety window and passes the real inter-trial gap.
        ns.configure_auto_drift(
            enabled=True,
            interval=args.sample_interval,
            min_pause=args.drift_min_pause,
        )
        print(f'Event send mode: {send_mode}')
        print('Press q or escape to stop early; the log is still written.')
        if args.ntpsync_every:
            print(
                'Diagnostic mode: sending ECI ntpsync before every '
                f'{args.ntpsync_every} stimulus/stimuli.'
            )
        if args.ntpsync_after_every:
            print(
                'Diagnostic mode: sending ECI ntpsync after every '
                f'{args.ntpsync_after_every} stimulus/stimuli.'
            )

        win = visual.Window(
            fullscr=args.fullscreen,
            screen=args.screen,
            units='height',
            color='black',
        )
        dot = visual.Circle(
            win,
            radius=args.dot_radius,
            pos=tuple(args.dot_pos),
            fillColor='white',
            lineColor='white',
            edges=64,
        )
        exp_clock = core.MonotonicClock()
        isis = build_isi_sequence(args.duration)
        next_onset = 0.0
        skipped = {'pause_too_short': 0}

        def log_drift_sample(label: str, sample: dict) -> None:
            record = {
                'trial': '',
                'phase': label,
                'send_mode': send_mode,
                'planned_onset': '',
                'psychopy_time': exp_clock.getTime(),
                'package_time': ns.getTime(),
                'ntp_offset': sample.get('offset'),
                'ntp_delay': sample.get('delay'),
                'ntp_valid': sample.get('valid'),
                'ntp_reject_reason': sample.get('reject_reason'),
                'ntp_burst_size': sample.get('burst_size'),
                'ntp_burst_ok': sample.get('burst_ok'),
                'ntp_burst_worst_delay': sample.get('burst_worst_delay'),
                'ntp_offset_mono': sample.get('offset_mono'),
                'ntp_sample_sys_mono_skew': sample.get('sys_mono_skew'),
            }
            add_clock_diagnostics(record, ns)
            records.append(record)

        def record_drift_sample_if_due(available_pause: float) -> None:
            status = ns.sample_drift_if_due(available_pause=available_pause)
            if status.get('sampled'):
                log_drift_sample('drift_sample', status['sample'])
            elif status.get('reason') == 'pause_too_short':
                skipped['pause_too_short'] += 1

        log_drift_sample('drift_sample_start', ns.sample_drift())

        for trial, isi in enumerate(isis, 1):
            next_onset += isi

            sync_before_stimulus = (
                args.ntpsync_every > 0 and
                (trial - 1) % args.ntpsync_every == 0
            )
            sync_local_time = ''
            sync_result = ''
            sync_error = ''
            if sync_before_stimulus:
                sync_local_time = time.time()
                try:
                    sync_result = repr(ns.ntpsync())
                except Exception as err:
                    sync_error = f'{type(err).__name__}: {err}'

            # Hold the black screen until the scheduled onset, refreshing
            # every frame so the flip that shows the dot is on schedule.
            while exp_clock.getTime() < next_onset:
                if event.getKeys(keyList=QUIT_KEYS):
                    raise KeyboardInterrupt
                win.flip()

            record = {
                'trial': trial,
                'phase': 'dot_on',
                'send_mode': send_mode,
                'planned_onset': next_onset,
                'sync_before_stimulus': sync_before_stimulus,
                'sync_after_stimulus': (
                    args.ntpsync_after_every > 0 and
                    trial % args.ntpsync_after_every == 0
                ),
                'sync_local_time': sync_local_time,
                'sync_result': sync_result,
                'sync_error': sync_error,
            }

            def mark_onset(rec=record):
                # Runs on the flip that makes the dot visible. Call
                # send_event() straight from here -- that is the pattern
                # this test exists to validate -- and time the call, since
                # that span is what a real experiment pays at every onset.
                span_start = time.monotonic()
                try:
                    result = ns.send_event(
                        event_type='stm+',
                        label='stm+',
                        desc='white dot onset',
                    )
                except Exception as err:
                    span_end = time.monotonic()
                    rec['send_error'] = f'{type(err).__name__}: {err}'
                else:
                    span_end = time.monotonic()
                    if result is not None:
                        rec['send_result'] = repr(result)
                rec['flip_monotonic_time'] = span_start
                rec['send_call_span_ms'] = (span_end - span_start) * 1000.0
                # Read a few microseconds after the flip; the system-minus-
                # monotonic skew this feeds is stable at that scale.
                rec['flip_local_time'] = time.time()
                rec['psychopy_time'] = exp_clock.getTime()
                rec['pending_events'] = ns.pending_events()

            dot.draw()
            win.callOnFlip(mark_onset)
            win.flip()

            dot_off = next_onset + args.dot_duration
            while exp_clock.getTime() < dot_off:
                if event.getKeys(keyList=QUIT_KEYS):
                    raise KeyboardInterrupt
                dot.draw()
                win.flip()
            win.flip()

            if record['sync_after_stimulus']:
                record['post_sync_local_time'] = time.time()
                try:
                    record['post_sync_result'] = repr(ns.ntpsync())
                except Exception as err:
                    record['post_sync_error'] = f'{type(err).__name__}: {err}'

            # Everything below here is off the critical path.
            if 'flip_monotonic_time' in record:
                record['package_time'] = ns.time_at_monotonic(
                    record['flip_monotonic_time']
                )
            add_clock_diagnostics(record, ns)
            records.append(record)

            # Idle time between the dot going off and the next dot onset.
            if trial < len(isis):
                available_pause = (
                    next_onset + isis[trial] - exp_clock.getTime()
                )
            else:
                available_pause = None
            record_drift_sample_if_due(available_pause)

        ns.flush_events()
        log_drift_sample('drift_sample_end', ns.sample_drift())

        summarize_send_timing(records, send_mode)
        print(
            '\nDrift samples skipped for short ITI:',
            skipped['pause_too_short'],
        )
        errors = ns.event_errors()
        if errors:
            print(f'WARNING: {len(errors)} asynchronous event sends failed.')
            for item in errors[:5]:
                print('  ', item)
        else:
            print('Asynchronous event send errors: none')
        print('Drift estimate:', ns.drift_estimate())
        return 0
    except KeyboardInterrupt:
        print('\nExperiment stopped early by the operator.', file=sys.stderr)
        return 130
    finally:
        if win is not None:
            win.close()
        if ns is not None and ns._connected:
            try:
                ns.end_rec()
            except Exception as err:
                print(f'EndRecording failed: {type(err).__name__}: {err}',
                      file=sys.stderr)
            try:
                ns.disconnect()
            except Exception as err:
                print(f'Disconnect failed: {type(err).__name__}: {err}',
                      file=sys.stderr)
        write_records(args.log, records)


if __name__ == '__main__':
    raise SystemExit(main())
