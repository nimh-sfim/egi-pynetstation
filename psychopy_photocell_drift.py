#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""PsychoPy photocell timing test for EGI NetStation drift correction.

Shows a black screen with a white dot. Every dot onset queues an ECI event
with event code ``stm+``. The event timestamp is captured on the PsychoPy
flip callback and sent asynchronously, so the network write does not hold up
the visual flip.
"""

import csv
import queue
import sys
import threading
import time
from argparse import ArgumentParser
from pathlib import Path

from egi_pynetstation.NetStation import NetStation


def connect_with_drift_options(ns: NetStation, ntp_ip: str, args) -> None:
    """Connect, using drift options when the installed package supports them."""
    try:
        ns.connect(
            ntp_ip=ntp_ip,
            handshake=True,
            drift_correction=not args.no_drift_correction,
            drift_min_samples=args.drift_min_samples,
            drift_min_span=args.drift_min_span,
        )
    except TypeError as err:
        if 'drift_correction' not in str(err):
            raise
        print(
            'Warning: imported NetStation.connect() does not accept '
            'drift_correction. Falling back to the older connect() API. '
            'Install this repository with `pip install -e .` to use the '
            'built-in drift corrector.',
            file=sys.stderr,
        )
        ns.connect(ntp_ip=ntp_ip)
        if hasattr(ns, 'set_drift_requirements'):
            ns.set_drift_requirements(
                args.drift_min_samples,
                args.drift_min_span,
            )
        if hasattr(ns, 'set_drift_correction'):
            ns.set_drift_correction(not args.no_drift_correction)


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


class EventSender:
    """Send ECI events from a worker thread using pre-captured timestamps."""

    def __init__(self, ns: NetStation, records: list):
        self._ns = ns
        self._records = records
        self._queue = queue.Queue()
        self._stop = object()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def put(self, record: dict) -> None:
        self._queue.put(record)

    def close(self) -> None:
        self._queue.put(self._stop)
        self._thread.join()

    def join(self) -> None:
        self._queue.join()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is self._stop:
                    return
                try:
                    result = self._ns.send_event(
                        start=item['package_time'],
                        event_type='stm+',
                        label='stm+',
                        desc='white dot onset',
                    )
                except Exception as err:
                    item['send_ok'] = False
                    item['send_error'] = f'{type(err).__name__}: {err}'
                else:
                    item['send_ok'] = True
                    item['send_result'] = repr(result)
                item['sent_local_time'] = time.time()
            finally:
                self._queue.task_done()


def write_records(path: str, records: list) -> None:
    if not path:
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    keys = [
        'trial',
        'phase',
        'planned_onset',
        'psychopy_time',
        'package_time',
        'sent_local_time',
        'send_ok',
        'send_result',
        'send_error',
        'ntp_offset',
        'ntp_delay',
        'sync_before_stimulus',
        'sync_after_stimulus',
        'sync_local_time',
        'sync_result',
        'sync_error',
        'post_sync_local_time',
        'post_sync_result',
        'post_sync_error',
    ]
    with open(path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=keys, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(records)


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


def main() -> int:
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
    parser.add_argument('--sample-interval', type=float, default=30.0)
    parser.add_argument('--drift-min-samples', type=int, default=4)
    parser.add_argument('--drift-min-span', type=float, default=90.0)
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
        '--no-drift-correction',
        action='store_true',
        help='Disable client-side NTP drift correction',
    )
    parser.add_argument('--fullscreen', action='store_true')
    parser.add_argument('--screen', type=int, default=0)
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--error-log', help='JSON-lines ECI error log path')
    parser.add_argument('--log', help='CSV file for PsychoPy/ECI event timing')
    args = parser.parse_args()
    if args.ntpsync_every < 0:
        parser.error('--ntpsync-every must be >= 0')
    if args.ntpsync_after_every < 0:
        parser.error('--ntpsync-after-every must be >= 0')

    ip_cmd, ip_clock, port = resolve_network(args)
    records = []
    ns = None
    sender = None
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

        sender = EventSender(ns, records)

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
        last_sample = 0.0

        def record_drift_sample(label: str) -> None:
            # NTP drift samples are collected between stimulus onsets. They do
            # not send ECI clock-sync commands and should not create markers.
            sample = ns.sample_drift()
            records.append({
                'trial': '',
                'phase': label,
                'planned_onset': '',
                'psychopy_time': exp_clock.getTime(),
                'package_time': ns.getTime(),
                'ntp_offset': sample.get('offset'),
                'ntp_delay': sample.get('delay'),
            })

        record_drift_sample('drift_sample_start')

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
            while exp_clock.getTime() < next_onset:
                if event.getKeys(keyList=['escape']):
                    raise KeyboardInterrupt
                win.flip()

            record = {
                'trial': trial,
                'phase': 'dot_on',
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
                # This callback runs on the flip that makes the dot visible.
                # Capture the package timestamp here, then let the worker
                # thread write to the ECI socket after the frame has flipped.
                rec['psychopy_time'] = exp_clock.getTime()
                rec['package_time'] = ns.getTime()
                records.append(rec)
                sender.put(rec)

            dot.draw()
            win.callOnFlip(mark_onset)
            win.flip()

            dot_off = next_onset + args.dot_duration
            while exp_clock.getTime() < dot_off:
                dot.draw()
                win.flip()
            win.flip()

            if record['sync_after_stimulus']:
                record['post_sync_local_time'] = time.time()
                try:
                    record['post_sync_result'] = repr(ns.ntpsync())
                except Exception as err:
                    record['post_sync_error'] = f'{type(err).__name__}: {err}'

            now = exp_clock.getTime()
            if now - last_sample >= args.sample_interval:
                record_drift_sample('drift_sample')
                last_sample = now

        sender.join()
        record_drift_sample('drift_sample_end')
        print('Drift estimate:', ns.drift_estimate())
        return 0
    except KeyboardInterrupt:
        print('\nExperiment interrupted.', file=sys.stderr)
        return 130
    finally:
        if sender is not None:
            sender.close()
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
