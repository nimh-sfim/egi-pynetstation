#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Report whether this machine's clocks are good enough for ECI timing.

Run this on any stimulus computer before trusting drift-corrected event
timestamps. The drift corrector assumes sub-millisecond resolution from
both time.time() and time.monotonic(); several Windows/Python combinations
provide ~15.6 ms instead, which silently degrades the correction.

    python -m egi_pynetstation.check_clocks
    python -m egi_pynetstation.check_clocks --compare

``--compare`` is the Windows diagnostic. On pre-3.13 Python, time.time()
and time.monotonic() are updated on the system timer tick, so their
resolution depends on whether something has raised the global timer
resolution with timeBeginPeriod(). This mode measures the clocks as they
are, then after importing PsychoPy, then after raising the timer
resolution here -- so you can see exactly which of those is responsible
for what.
"""

import argparse
import platform
import statistics
import sys
import time

WINDOWS = platform.system() == 'Windows'


def measured_resolution(func, samples=100000):
    """Smallest non-zero step actually observed from a clock function."""
    prev = func()
    smallest = None
    for _ in range(samples):
        now = func()
        delta = now - prev
        if delta > 0:
            if smallest is None or delta < smallest:
                smallest = delta
            prev = now
    return smallest


def sleep_accuracy(requested=0.05, trials=10):
    errors = []
    for _ in range(trials):
        start = time.perf_counter()
        time.sleep(requested)
        errors.append((time.perf_counter() - start - requested) * 1000.0)
    return statistics.mean(errors), max(errors)


def skew_jitter(reads=2000):
    """Spread of (system clock - monotonic clock) over a burst of reads.

    This difference is what cancels OS clock discipline out of the drift
    model, so it has to be measurable at sub-millisecond scale.
    """
    skews = [time.time() - time.monotonic() for _ in range(reads)]
    return (max(skews) - min(skews)) * 1000.0


def windows_timer_resolution():
    """Current Windows timer resolution in ms, via NtQueryTimerResolution.

    Returns (coarsest, finest, current) in milliseconds, or None off
    Windows. This is a direct reading rather than an inference from clock
    behaviour, so it is the authoritative answer to "has anything called
    timeBeginPeriod?".
    """
    if not WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        ntdll = ctypes.WinDLL('ntdll')
        coarsest = wintypes.ULONG()
        finest = wintypes.ULONG()
        current = wintypes.ULONG()
        ntdll.NtQueryTimerResolution(
            ctypes.byref(coarsest), ctypes.byref(finest),
            ctypes.byref(current),
        )
        # Units are 100 ns.
        return (coarsest.value / 10000.0, finest.value / 10000.0,
                current.value / 10000.0)
    except Exception as err:
        print(f'  could not query timer resolution: {err}')
        return None


def raise_timer_resolution(period_ms=1):
    """Ask Windows for a finer timer tick. Returns True on success."""
    if not WINDOWS:
        return False
    try:
        import ctypes
        return ctypes.WinDLL('winmm').timeBeginPeriod(period_ms) == 0
    except Exception as err:
        print(f'  could not raise timer resolution: {err}')
        return False


def release_timer_resolution(period_ms=1):
    if not WINDOWS:
        return
    try:
        import ctypes
        ctypes.WinDLL('winmm').timeEndPeriod(period_ms)
    except Exception:
        pass


def sample_clocks(samples=100000):
    return {name: measured_resolution(getattr(time, name), samples)
            for name in ('time', 'monotonic', 'perf_counter')}


def format_row(label, values, timer_ms=None):
    parts = '  '.join(
        f'{name}={value:.2e}s' if value is not None else f'{name}=?'
        for name, value in values.items()
    )
    timer = '' if timer_ms is None else f'   timer_tick={timer_ms:.3f}ms'
    return f'  {label:<28} {parts}{timer}'


def run_comparison() -> int:
    """Attribute clock resolution to the thing that actually sets it."""
    print('Comparing clock resolution before and after PsychoPy, and '
          'after raising\nthe timer resolution explicitly.\n')
    if not WINDOWS:
        print('  NOTE: this comparison only means something on Windows.')
        print('  Elsewhere the clocks do not depend on the timer tick, so '
              'all rows\n  should look the same.\n')

    timer = windows_timer_resolution()
    if timer:
        coarsest, finest, current = timer
        print(f'  Windows timer: current={current:.3f} ms  '
              f'(finest available {finest:.3f} ms, '
              f'coarsest {coarsest:.3f} ms)\n')

    baseline = sample_clocks()
    print(format_row('as launched', baseline,
                     timer[2] if timer else None))

    try:
        import psychopy  # noqa: F401
        from psychopy import core  # noqa: F401
        after_psychopy = sample_clocks()
        timer = windows_timer_resolution()
        print(format_row('after importing PsychoPy', after_psychopy,
                         timer[2] if timer else None))
    except Exception as err:
        after_psychopy = None
        print(f'  after importing PsychoPy      not available: '
              f'{type(err).__name__}')

    raised = raise_timer_resolution(1)
    if raised:
        after_raise = sample_clocks()
        timer = windows_timer_resolution()
        print(format_row('after timeBeginPeriod(1)', after_raise,
                         timer[2] if timer else None))
        release_timer_resolution(1)
    else:
        after_raise = None
        if WINDOWS:
            print('  after timeBeginPeriod(1)     could not raise')

    print()
    if not WINDOWS:
        print('Not Windows: clock resolution here does not depend on the '
              'timer tick.')
        return 0

    def coarse(values):
        return values and any(
            v is not None and v > 1e-3 for v in values.values()
        )

    if not coarse(baseline):
        print('Clocks are already sub-millisecond as launched. Nothing to do.')
        return 0

    print('Clocks are COARSE as launched, so drift correction will carry '
          'several\nmilliseconds of error. Findings:')
    if after_psychopy is not None:
        if coarse(after_psychopy):
            print('  - Importing PsychoPy did NOT improve them. PsychoPy '
                  'raises thread\n    priority (rush()), not timer '
                  'resolution, so do not rely on it.')
        else:
            print('  - Importing PsychoPy improved them.')
    if after_raise is not None and not coarse(after_raise):
        print('  - timeBeginPeriod(1) DID improve them. Call it at the start '
              'of your\n    experiment, or upgrade to Python 3.13+, which '
              'does not depend on\n    the timer tick at all.')
    return 1


def run_report() -> int:
    print(f'python   : {sys.version.split()[0]}')
    print(f'platform : {platform.platform()}')
    print()

    problems = []

    timer = windows_timer_resolution()
    if timer:
        coarsest, finest, current = timer
        print(f'windows timer tick : current={current:.3f} ms  '
              f'(finest {finest:.3f} ms)')
        if current > 2.0:
            problems.append(
                f'the Windows timer tick is {current:.2f} ms; on Python '
                f'< 3.13 this sets the resolution of time.time() and '
                f'time.monotonic(). Call timeBeginPeriod(1) or upgrade '
                f'Python'
            )
        print()

    for name in ('time', 'monotonic', 'perf_counter'):
        info = time.get_clock_info(name)
        actual = measured_resolution(getattr(time, name))
        print(f'{name:13} impl={info.implementation}')
        print(f'{"":13} claimed={info.resolution:.3e} s  '
              f'measured={actual:.3e} s  '
              f'monotonic={info.monotonic}')
        # 15.6 ms is the classic Windows low-resolution timer tick.
        if actual is not None and actual > 1e-3:
            problems.append(
                f'{name}() resolution is {actual * 1000:.2f} ms; '
                f'drift correction needs better than 1 ms'
            )

    print()
    mean_err, max_err = sleep_accuracy()
    print(f'sleep(0.05)  : mean overshoot {mean_err:.2f} ms, '
          f'max {max_err:.2f} ms')
    # A few ms of overshoot is normal and harmless. Overshoot near the
    # 15.6 ms Windows timer tick means sleep() is being rounded up to the
    # legacy timer resolution, which lengthens every NTP burst.
    if max_err > 12.0:
        problems.append(
            f'time.sleep() overshoots by up to {max_err:.1f} ms, near the '
            f'15.6 ms legacy Windows timer tick; NTP bursts will be slower '
            f'than requested'
        )

    spread = skew_jitter()
    print(f'system-monotonic skew jitter over 2000 reads: {spread:.4f} ms')
    if spread > 1.0:
        problems.append(
            f'system/monotonic skew jitters by {spread:.2f} ms; '
            f'OS clock-discipline cancellation will be noisy'
        )

    print()
    if sys.version_info < (3, 13) and WINDOWS:
        problems.append(
            'Python < 3.13 on Windows: time.time() and time.monotonic() '
            'are updated on the system timer tick. Upgrade to 3.13 or '
            'newer, or run --compare to see what raising the tick would buy'
        )

    if problems:
        print('PROBLEMS FOUND:')
        for item in problems:
            print(f'  - {item}')
        if WINDOWS:
            print('\nRun with --compare to attribute this to a specific '
                  'cause.')
        return 1
    print('Clocks look suitable for drift-corrected ECI timing.')
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description='Check clock resolution for drift-corrected ECI timing.'
    )
    parser.add_argument(
        '--compare',
        action='store_true',
        help=(
            'Measure the clocks as launched, after importing PsychoPy, and '
            'after calling timeBeginPeriod(1), to attribute the resolution '
            'to a specific cause. Windows diagnostic.'
        ),
    )
    args = parser.parse_args(argv)
    return run_comparison() if args.compare else run_report()


if __name__ == '__main__':
    raise SystemExit(main())
