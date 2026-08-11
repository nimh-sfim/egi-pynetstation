#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Report whether this machine's clocks are good enough for ECI timing.

Run this on any stimulus computer before trusting drift-corrected event
timestamps. The drift corrector assumes sub-millisecond resolution from
both time.time() and time.monotonic(); several Windows/Python combinations
provide ~15.6 ms instead, which silently destroys the correction.
"""

import platform
import statistics
import sys
import time


def measured_resolution(func, samples=200000):
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


def main() -> int:
    print(f'python   : {sys.version.split()[0]}')
    print(f'platform : {platform.platform()}')
    print()

    problems = []

    for name in ('time', 'monotonic', 'perf_counter'):
        info = time.get_clock_info(name)
        func = getattr(time, name)
        actual = measured_resolution(func)
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

    # The frame correction depends on this difference being measurable at
    # sub-millisecond scale, since it is what cancels OS clock discipline.
    skews = []
    for _ in range(2000):
        skews.append(time.time() - time.monotonic())
    spread = (max(skews) - min(skews)) * 1000.0
    print(f'system-monotonic skew jitter over 2000 reads: {spread:.4f} ms')
    if spread > 1.0:
        problems.append(
            f'system/monotonic skew jitters by {spread:.2f} ms; '
            f'OS clock-discipline cancellation will be noisy'
        )

    print()
    if sys.version_info < (3, 13) and platform.system() == 'Windows':
        problems.append(
            'Python < 3.13 on Windows: time.time() and time.monotonic() '
            'use low-resolution timers. Upgrade to 3.13 or newer.'
        )

    if problems:
        print('PROBLEMS FOUND:')
        for item in problems:
            print(f'  - {item}')
        return 1
    print('Clocks look suitable for drift-corrected ECI timing.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
