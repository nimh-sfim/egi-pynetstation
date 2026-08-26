#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Classify a photocell timing run from its Net Station and PsychoPy logs.

Takes the Net Station marker-offset export and, optionally, the CSV and
JSON-lines error log written by ``example5_psychopy_photocell_drift.py``,
and reports the handful of numbers that actually discriminate between the
things that go wrong in this test:

- a **constant** offset, which is display and photocell geometry and is a
  calibration number rather than a fault
- a **trend**, which is two clocks running at different rates
- **jitter**, whose *shape* says where it comes from: a flat band one
  refresh period wide means the stimulus is not phase-locked to scanout,
  separate modes a refresh apart mean dropped frames, and a bell means an
  analogue problem such as a dim panel or a partly covered photodiode

Those three are independent. Reading a single standard deviation blends
them together and points at the wrong subsystem, which is the mistake this
script exists to prevent.

The client-clock and package-health sections exist to *rule the package
out* quickly. Drift-model residuals stay in the tens of microseconds even
while the host system clock is being slewed by tens of milliseconds an
hour, so if those residuals are small the timing error is downstream of
this package and no amount of drift tuning will move it.

Usage
-----
    python experiments/analyze_photocell_run.py OFFSET.txt \
        --csv RUN.csv --error-log RUN.jsonl

Only the offset export is required; each extra file enables more sections.
Standard library only, so it runs in whatever interpreter is on the
recording machine.
"""

import csv
import json
import math
import statistics
from argparse import ArgumentParser
from collections import Counter
from pathlib import Path

# Jitter one refresh period wide is the signature this script is looking
# for, but a real run never lands on exactly 1.00, so accept a band.
FRAME_SPAN_LOW = 0.70
FRAME_SPAN_HIGH = 1.30
# Excess kurtosis: a continuous uniform sits at -1.2, a Gaussian at 0.0.
# Anything below this is closer to a flat band than to a bell.
FLAT_KURTOSIS = -0.60
# Detrended scatter at or below this is the validated-rig figure and needs
# no explanation.
HEALTHY_JITTER_MS = 1.5
# Drift-model residuals below this mean the package's clock model is
# tracking cleanly, whatever the photocell says.
HEALTHY_RESIDUAL_MS = 0.30


def parse_offset_export(path: str) -> list:
    """Read a Net Station marker-offset export.

    Columns are located by header name rather than position, since the
    export's column set depends on what was selected in Net Station. The
    trailing ``Average:``/``Median:`` summary rows are skipped: they carry
    no timestamp, so they cannot be placed on the time axis.

    Returns
    -------
    list of (seconds_from_first_marker, offset_ms)
    """
    lines = [
        line.split('\t')
        for line in Path(path).read_text().splitlines()
        if line.strip()
    ]
    if not lines:
        raise ValueError(f'{path} is empty')
    header = [cell.strip() for cell in lines[0]]
    try:
        i_time = header.index('Rel. Time')
        i_offset = header.index('Offset')
    except ValueError as err:
        raise ValueError(
            f'{path} does not look like a Net Station offset export; '
            f'expected "Rel. Time" and "Offset" columns, found {header}'
        ) from err

    samples = []
    for row in lines[1:]:
        if len(row) <= max(i_time, i_offset):
            continue
        stamp = row[i_time].strip()
        # Summary rows leave this cell blank or non-temporal.
        if stamp.count(':') != 2:
            continue
        hours, minutes, seconds = stamp.split(':')
        try:
            when = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
            offset = float(row[i_offset])
        except ValueError:
            continue
        samples.append((when, offset))

    if len(samples) < 2:
        raise ValueError(f'{path} yielded fewer than two usable markers')
    samples.sort()
    origin = samples[0][0]
    return [(when - origin, offset) for when, offset in samples]


def linear_fit(xs: list, ys: list):
    """Ordinary least squares. Returns (slope, intercept, residuals)."""
    mean_x = statistics.mean(xs)
    mean_y = statistics.mean(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return 0.0, mean_y, [y - mean_y for y in ys]
    slope = sum(
        (x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)
    ) / denominator
    intercept = mean_y - slope * mean_x
    residuals = [y - (intercept + slope * x) for x, y in zip(xs, ys)]
    return slope, intercept, residuals


def excess_kurtosis(values: list) -> float:
    """Excess kurtosis: 0.0 for a Gaussian, -1.2 for a flat distribution."""
    n = len(values)
    if n < 4:
        return float('nan')
    mean = statistics.mean(values)
    variance = sum((v - mean) ** 2 for v in values) / n
    if variance == 0:
        return float('nan')
    fourth = sum((v - mean) ** 4 for v in values) / n
    return fourth / variance ** 2 - 3.0


def find_modes(residuals: list, frame_period_ms: float) -> int:
    """Count clusters separated by at least half a refresh period.

    Dropped frames put markers into groups a whole refresh apart, with
    empty space between them. One continuous band means the error is
    sub-frame and dropped frames are not the explanation.
    """
    if frame_period_ms <= 0:
        return 1
    ordered = sorted(residuals)
    gap = frame_period_ms * 0.5
    return 1 + sum(
        1 for a, b in zip(ordered, ordered[1:]) if (b - a) > gap
    )


def as_float(row: dict, key: str):
    """Read one CSV cell as a float, or None if it is blank or absent."""
    try:
        return float(row.get(key, ''))
    except (TypeError, ValueError):
        return None


def histogram(residuals: list, width: int = 46) -> None:
    counts = Counter(int(round(r)) for r in residuals)
    peak = max(counts.values())
    for key in range(min(counts), max(counts) + 1):
        count = counts.get(key, 0)
        bar = '#' * int(round(width * count / peak))
        print(f'    {key:>+5} ms | {count:>5} {bar}')


def report_offsets(samples: list, frame_period_ms: float) -> dict:
    """Split the photocell offsets into constant, trend, and jitter."""
    times = [t for t, _ in samples]
    offsets = [o for _, o in samples]
    span_s = times[-1] - times[0]

    print('\n=== PHOTOCELL OFFSET ===')
    print(f'  markers            {len(samples)}')
    print(f'  span               {span_s:.0f} s ({span_s / 3600:.2f} h)')
    print(f'  mean               {statistics.mean(offsets):.2f} ms')
    print(f'  median             {statistics.median(offsets):.2f} ms')
    print(f'  raw SD             {statistics.pstdev(offsets):.2f} ms')

    slope, intercept, residuals = linear_fit(times, offsets)
    jitter = statistics.pstdev(residuals)
    span_ms = max(residuals) - min(residuals)
    kurtosis = excess_kurtosis(residuals)
    modes = find_modes(residuals, frame_period_ms)

    print('\n  --- separated ---')
    print(f'  constant (fit @ t0){intercept:>8.2f} ms   '
          '<- display + photocell geometry; calibration, not a fault')
    print(f'  trend              {slope * 3600:>+8.2f} ms/h  '
          f'({abs(slope) * 1000:.2f} ppm)   <- relative clock rate error')
    print(f'  detrended jitter   {jitter:>8.2f} ms   '
          '<- presentation stability')

    print('\n=== JITTER SHAPE ===')
    print(f'  span               {span_ms:.1f} ms', end='')
    if frame_period_ms > 0:
        print(f' = {span_ms / frame_period_ms:.2f} refresh periods'
              f' (at {frame_period_ms:.3f} ms)')
    else:
        print('  (no CSV, refresh period unknown)')
    print(f'  excess kurtosis    {kurtosis:+.2f}   '
          '(0.0 = bell, -1.2 = flat band)')
    print(f'  clusters           {modes}')
    histogram(residuals)

    return {
        'trend_ms_per_h': slope * 3600,
        'jitter_ms': jitter,
        'span_ms': span_ms,
        'kurtosis': kurtosis,
        'modes': modes,
        'constant_ms': intercept,
    }


def report_presentation(rows: list) -> dict:
    """Compare the realized refresh rate against the calibrated estimate.

    The realized rate comes from frame counts and monotonic timestamps
    over the whole run, so it is far more precise than the short burst
    ``measure_display()`` uses at startup.
    """
    onsets = [
        (as_float(r, 'flip_monotonic_time'), as_float(r, 'onset_frame'))
        for r in rows if r.get('phase') == 'dot_on'
    ]
    onsets = [(t, f) for t, f in onsets if t is not None and f is not None]
    result = {'frame_period_ms': 0.0}
    if len(onsets) < 2:
        return result

    elapsed = onsets[-1][0] - onsets[0][0]
    frames = onsets[-1][1] - onsets[0][1]
    if elapsed <= 0 or frames <= 0:
        return result
    realized_fps = frames / elapsed
    realized_period_ms = 1000.0 / realized_fps

    measured_fps = next(
        (as_float(r, 'measured_fps') for r in rows
         if as_float(r, 'measured_fps')), None
    )

    print('\n=== PRESENTATION ===')
    print(f'  frames             {frames:.0f} over {elapsed:.0f} s')
    print(f'  realized refresh   {realized_fps:.6f} Hz '
          f'({realized_period_ms:.6f} ms)')
    if measured_fps:
        error_ppm = (measured_fps - realized_fps) / realized_fps * 1e6
        drift_ms = (frames * (1000.0 / measured_fps) - elapsed * 1000.0)
        print(f'  calibrated at      {measured_fps:.6f} Hz '
              f'({error_ppm:+.0f} ppm error)')
        print(f'  schedule drift     {drift_ms:+.0f} ms over the run '
              '<- affects onset scheduling only, not marker accuracy')
    result['frame_period_ms'] = realized_period_ms
    return result


def report_client_clock(rows: list) -> None:
    """Rate of change of the host system clock against the monotonic clock.

    A non-zero rate here means the operating system is slewing its clock,
    typically because an NTP daemon is disciplining it. The package works
    in the monotonic frame precisely so that this cannot reach event
    timestamps -- so a large value here alongside small drift-model
    residuals below is the expected, healthy pattern, not a fault.
    """
    skews = []
    for row in rows:
        if row.get('phase') != 'dot_on':
            continue
        local = as_float(row, 'flip_local_time')
        mono = as_float(row, 'flip_monotonic_time')
        if local is not None and mono is not None:
            skews.append((mono, (local - mono) * 1000.0))
    if len(skews) < 2:
        return

    slope, _, residuals = linear_fit(
        [t for t, _ in skews], [s for _, s in skews]
    )
    total = skews[-1][1] - skews[0][1]
    print('\n=== CLIENT SYSTEM CLOCK ===')
    print(f'  sys-mono slew      {slope * 3600:>+8.2f} ms/h '
          f'({total:+.1f} ms over the run)')
    print(f'  scatter about fit  {statistics.pstdev(residuals):>8.2f} ms')
    if abs(slope * 3600) > 1.0:
        print('  note: the OS is actively slewing the system clock. The '
              'package uses the')
        print('        monotonic clock, so this does not reach event '
              'timestamps -- confirm')
        print('        via the drift-model residuals below.')


def report_package_health(rows: list, error_log: str) -> dict:
    """Everything that would indicate the package itself is at fault."""
    dot_rows = [r for r in rows if r.get('phase') == 'dot_on']
    print('\n=== PACKAGE HEALTH ===')

    spans = [
        as_float(r, 'send_call_span_ms') for r in dot_rows
        if as_float(r, 'send_call_span_ms') is not None
    ]
    if spans:
        ordered = sorted(spans)
        print(f'  flip callback block  median {statistics.median(spans) * 1000:.1f} us'
              f'   p99 {ordered[int(0.99 * len(ordered))] * 1000:.1f} us'
              f'   max {max(spans) * 1000:.1f} us')

    pending = [
        as_float(r, 'pending_events') for r in dot_rows
        if as_float(r, 'pending_events') is not None
    ]
    if pending:
        print(f'  max queue depth      {max(pending):.0f}')
    errors = sum(1 for r in dot_rows if r.get('send_error'))
    print(f'  send errors          {errors}')

    residuals = [
        as_float(r, 'drift_model_rms_residual_ms') for r in dot_rows
        if as_float(r, 'drift_model_rms_residual_ms') is not None
    ]
    worst_residual = None
    if residuals:
        worst_residual = max(residuals)
        print(f'  drift RMS residual   median '
              f'{statistics.median(residuals):.3f} ms   max '
              f'{worst_residual:.3f} ms')

    delays = [
        as_float(r, 'ntp_delay') for r in rows
        if as_float(r, 'ntp_delay') is not None
    ]
    if delays:
        print(f'  NTP round trip       median '
              f'{statistics.median(delays) * 1000:.2f} ms   max '
              f'{max(delays) * 1000:.2f} ms   (n={len(delays)})')

    if dot_rows:
        last = dot_rows[-1]
        print(f"  fits                 {last.get('drift_accepted_fits')} "
              f"accepted, {last.get('drift_rejected_fits')} rejected")
        reasons = Counter(
            r['drift_last_reject_reason'] for r in dot_rows
            if r.get('drift_last_reject_reason')
        )
        if reasons:
            print(f'  reject reasons       {dict(reasons)}')
        correction = as_float(last, 'drift_correction_ms')
        if correction is not None:
            print(f'  applied correction   {correction:+.3f} ms by end of run')

    if error_log:
        kinds = Counter()
        for line in Path(error_log).read_text().splitlines():
            if not line.strip():
                continue
            try:
                kinds[json.loads(line).get('record', '?')] += 1
            except json.JSONDecodeError:
                kinds['unparseable'] += 1
        print(f'  error-log records    {dict(kinds)}')
        # These are the records that mean the model itself gave up.
        alarming = {
            k: v for k, v in kinds.items()
            if 'stall' in k or 'outage' in k or 'failure' in k
        }
        if alarming:
            print(f'  ** attention:        {alarming}')

    return {'worst_residual_ms': worst_residual}


def verdict(offsets: dict, presentation: dict, health: dict) -> None:
    """Name the subsystem each symptom points at.

    These are heuristics over one run, not proof. They are worth stating
    anyway because each symptom has a different owner, and the common
    failure is to spend a day tuning the drift model for a problem that
    lives in the display.
    """
    print('\n=== VERDICT ===')
    frame_period = presentation.get('frame_period_ms', 0.0)

    print(f"  constant {offsets['constant_ms']:.1f} ms: display latency and "
          'photocell placement.')
    print('    Fixed offsets cannot come from clock error. Calibrate and '
          'subtract it, or')
    print('    chase it with panel brightness, photodiode coverage, and '
          'fullscreen.')

    trend = offsets['trend_ms_per_h']
    if abs(trend) < 2.0:
        print(f'\n  trend {trend:+.1f} ms/h: fine.')
    else:
        print(f'\n  trend {trend:+.1f} ms/h '
              f'({abs(trend) / 3.6:.1f} ppm): two clocks at different rates.')
        if health.get('worst_residual_ms') is None:
            print('    Whether the package tracked its NTP server through '
                  'this cannot be told')
            print('    from the offset export alone. Re-run with --csv; the '
                  'drift-model')
            print('    residuals are what separate a package problem from a '
                  'downstream one.')
        elif health['worst_residual_ms'] < HEALTHY_RESIDUAL_MS:
            print('    The drift model held sub-millisecond residuals, so it '
                  'is tracking the')
            print('    NTP server correctly and this trend is NOT in the '
                  'client-to-server')
            print('    pair. Look at the clock that places markers: whether '
                  'the Net Station')
            print('    host is disciplined, and to which source.')
        else:
            print('    Drift-model residuals are also elevated -- check NTP '
                  'reachability and')
            print('    round-trip delay before looking downstream.')

    jitter = offsets['jitter_ms']
    span_frames = (
        offsets['span_ms'] / frame_period if frame_period > 0 else 0.0
    )
    print(f'\n  jitter {jitter:.2f} ms:', end=' ')
    if jitter <= HEALTHY_JITTER_MS:
        print('healthy.')
    elif frame_period <= 0:
        # Every jitter branch below is a statement about frame geometry,
        # and the refresh period comes from the CSV. Guessing without it
        # would mean naming a subsystem on no evidence.
        print('unclassified.')
        print('    Telling a compositor problem from an analogue one needs '
              'the refresh')
        print('    period, which comes from the CSV. Re-run with --csv.')
    elif offsets['modes'] > 1:
        print(f"{offsets['modes']} clusters a refresh apart -- dropped "
              'frames.')
        print('    The presentation is skipping refreshes. Check GPU load, '
              'window size,')
        print('    and whether anything else is drawing on that display.')
    elif frame_period > 0 and FRAME_SPAN_LOW <= span_frames <= FRAME_SPAN_HIGH \
            and offsets['kurtosis'] < FLAT_KURTOSIS:
        print(f'flat band {span_frames:.2f} refresh periods wide.')
        print('    The marker is not phase-locked to scanout: the flip is '
              'timestamped when')
        print('    the buffer is queued, not when it is scanned out, so the '
              'photon comes')
        print('    out a uniformly random part of a frame later. This is the '
              'compositor.')
        print('    Run fullscreen, on a fixed refresh rate, with nothing '
              'else compositing.')
    else:
        print('broad and bell-shaped.')
        print('    Not frame geometry. Look at analogue causes: panel '
              'brightness and')
        print('    dimming, photodiode coverage and threshold, stimulus '
              'contrast.')


def main(argv=None) -> int:
    parser = ArgumentParser(
        description='Classify a photocell timing run for egi-pynetstation.'
    )
    parser.add_argument(
        'offsets', help='Net Station marker-offset export (tab separated)'
    )
    parser.add_argument(
        '--csv',
        help='CSV written by example5_psychopy_photocell_drift.py',
    )
    parser.add_argument(
        '--error-log', help='JSON-lines ECI error log from the same run'
    )
    args = parser.parse_args(argv)

    rows = []
    if args.csv:
        with open(args.csv, newline='', encoding='utf-8') as handle:
            rows = list(csv.DictReader(handle))

    print(f'photocell run: {Path(args.offsets).name}')
    presentation = report_presentation(rows) if rows else {
        'frame_period_ms': 0.0
    }
    samples = parse_offset_export(args.offsets)
    offsets = report_offsets(samples, presentation['frame_period_ms'])

    health = {}
    if rows:
        report_client_clock(rows)
        health = report_package_health(rows, args.error_log)
    else:
        print('\n(no --csv given: presentation, client clock, and package '
              'health skipped)')

    verdict(offsets, presentation, health)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
