# EGI PyNetStation

## About

`egi-pynetstation` is a Python interface for sending ECI commands and event
markers to EGI Net Station / Amp Server Pro.

The package supports NTP-based event timing. A single ECI `NTPClockSync`
establishes the event timestamp epoch, and client-side drift correction then
compensates for slow clock drift between the stimulus computer and the
Net Station / amplifier NTP server.

Validated over one-hour continuous recordings with a photocell: marker-to-
photocell offset held to a standard deviation of 0.94 ms with a residual
trend of +0.49 ms/hour, across a run in which the operating system stepped
the system clock by 256 ms.

## Installation

Install the current stable release from PyPI:

```bash
pip install egi-pynetstation
```

For local development or testing the current repository:

```bash
git clone https://github.com/nimh-sfim/egi-pynetstation.git
cd egi-pynetstation
pip install -e .
```

If you have previously installed the PyPI release into the same environment,
uninstall it before using an editable checkout. Otherwise the site-packages
copy can shadow your working tree depending on the current directory, and you
will silently run the wrong code:

```bash
pip uninstall -y egi-pynetstation
```

Verify which copy is actually loaded:

```bash
python -c "import importlib; m = importlib.import_module('egi_pynetstation.NetStation'); print(m.__file__)"
```

### Check Your Clocks First

The drift corrector assumes sub-millisecond resolution from both
`time.time()` and `time.monotonic()`. Some Python and Windows combinations
provide roughly 15.6 ms instead, which degrades timing silently rather than
raising an error. Run this once on any new stimulus computer:

```bash
python check_clocks.py
```

It reports measured resolution for each clock, `time.sleep()` overshoot, and
the jitter in the system-versus-monotonic clock difference. On Windows,
Python 3.13 or newer is strongly recommended: earlier versions used
low-resolution timers for both `time.time()` and `time.monotonic()`.

## Basic Use

```python
from egi_pynetstation import NetStation

IP_ns = '10.10.10.42'   # computer running Net Station
port_ns = 55513         # ECI TCP port configured in Net Station
IP_amp = '10.10.10.51'  # amplifier / Net Station NTP server

ns = NetStation(IP_ns, port_ns)
ns.connect(ntp_ip=IP_amp)

ns.begin_rec()
ns.send_event(event_type='STRT', start=0.0)

for trial in range(10):
    # Present your stimulus here.
    ns.send_event(event_type='STIM')

ns.end_rec()
ns.disconnect()
```

Event types must be exactly four ASCII characters. The default
`start="now"` uses `ns.getTime()` to timestamp the event.

---

# Integrating With Your Own PsychoPy Experiment

This section is the practical guide. It covers the four things that actually
determine timing accuracy, in the order they matter.

## The Short Version

```python
import queue
import threading
import time

from egi_pynetstation import NetStation
from psychopy import core, visual

ns = NetStation('10.10.10.42', 55513)
ns.connect(ntp_ip='10.10.10.51')       # drift correction on by default
ns.configure_auto_drift(enabled=True, interval=15.0, min_pause=0.35)
ns.begin_rec()

# --- worker thread: converts captured timestamps and sends events ---------
event_queue = queue.Queue()

def sender():
    while True:
        item = event_queue.get()
        if item is None:
            return
        code, monotonic_at_flip = item
        start = ns.time_at_monotonic(monotonic_at_flip)
        ns.send_event(start=start, event_type=code, label=code)

worker = threading.Thread(target=sender, daemon=True)
worker.start()

# --- stimulus loop --------------------------------------------------------
win = visual.Window(fullscr=True, screen=1, color='black')
stim = visual.Circle(win, radius=0.05, fillColor='white')

try:
    for trial in range(200):
        def mark_onset():
            # Runs on the flip that makes the stimulus visible.
            # Capture a raw clock reading only. Nothing else.
            event_queue.put(('stm+', time.monotonic()))

        stim.draw()
        win.callOnFlip(mark_onset)
        win.flip()

        # ... your inter-trial interval ...
        # Offer the package the idle time you can safely give up.
        ns.sample_drift_if_due(available_pause=iti_seconds_remaining)
finally:
    event_queue.put(None)
    worker.join()
    ns.end_rec()
    ns.disconnect()
```

## 1. Capture the Timestamp on the Flip, Send it Later

The single most important pattern. Your flip callback should capture a raw
`time.monotonic()` reading and nothing else — no network I/O, no locks, no
drift-model work.

`ns.time_at_monotonic(monotonic_time)` converts a previously captured
monotonic reading into an event timestamp. The resulting timestamp describes
the instant of capture, not the instant of conversion, so you can convert it
on a worker thread well after the frame has appeared.

```python
def mark_onset():
    event_queue.put(('stm+', time.monotonic()))   # cheap: ~microseconds

win.callOnFlip(mark_onset)
win.flip()
```

Calling `ns.getTime()` directly in the callback also works and is safe — the
package uses separate locks for socket I/O and clock state, so a send in
progress on another thread cannot stall a timestamp read. Measured worst case
is about 9 microseconds even while a worker thread is saturating the socket.
But `time_at_monotonic()` is still preferable, because it moves *all* of the
work off the critical path rather than merely making it fast.

Do **not** call `ns.clock_state()` or `ns.drift_estimate()` inside a flip
callback. Those build a full diagnostic dictionary. Call them from the worker
thread or between trials.

## 2. Send Events From a Worker Thread

`ns.send_event()` writes to a TCP socket and waits for the ECI response. Doing
that inline will delay your flip.

Pass the pre-captured timestamp via `start=`:

```python
start = ns.time_at_monotonic(monotonic_at_flip)
ns.send_event(start=start, event_type='stm+', label='stm+')
```

Because the timestamp is captured at the flip and carried through explicitly,
it does not matter when the send actually happens. Measured send latency on
the reference setup is 0.23 ms median, 0.45 ms maximum, but correctness does
not depend on that.

The `NetStation` object is safe to use from multiple threads.

## 3. Sample Drift During Safe Windows

Drift samples are NTP queries. They do **not** send ECI clock-sync commands
and do not create markers. But they do block the calling thread for roughly
170 ms at default settings, so they must not land near a flip.

Let the package own the schedule and your experiment own the safety window:

```python
ns.configure_auto_drift(enabled=True, interval=15.0, min_pause=0.35)

# In your inter-trial interval, once you know how much idle time is left:
status = ns.sample_drift_if_due(available_pause=iti_remaining)
```

The call returns without sampling if a sample is not due yet, or if the pause
you offered is shorter than `min_pause`. Return values:

```python
{'sampled': True,  'reason': 'due', 'sample': {...}}
{'sampled': False, 'reason': 'not_due', 'seconds_until_due': 12.4}
{'sampled': False, 'reason': 'pause_too_short', 'min_pause': 0.35}
{'sampled': False, 'reason': 'disabled'}
{'sampled': False, 'reason': 'not_synced'}
```

If you would rather manage the schedule yourself, call `ns.sample_drift()`
directly from a point you know is safe.

Each call makes several rapid NTP queries and keeps the lowest-delay reply.
NTP offset error tracks path asymmetry, which tracks round-trip delay, so the
fastest reply in a short burst is the most trustworthy. Selecting the minimum
is considerably better than averaging, which folds the bad replies back in.

```python
ns.set_drift_sampling(samples=4, spacing=0.05)   # defaults
```

A burst blocks for about `(samples - 1) * spacing` plus the round trips.
Budget for it when choosing `min_pause`.

**How often?** The model needs `drift_min_samples` valid samples spanning
`drift_min_span` seconds before it engages. At the defaults (13 samples,
180 s) with 15-second sampling, correction becomes active after about four
minutes. Sampling every 15 to 60 seconds is reasonable; more frequent
sampling mostly buys noise reduction on the slope estimate.

## 4. Prevent the Machine From Sleeping

On macOS, wrap your run:

```bash
caffeinate -dis python my_experiment.py
```

`-d` prevents display sleep, `-i` prevents idle sleep, `-s` prevents system
sleep (honored only on AC power). When a utility is given, the assertions are
held for exactly the duration of that process.

This matters for more than the screensaver. Python's `time.monotonic()` on
macOS does not advance while the machine is asleep, so a sleep mid-recording
would corrupt the elapsed-time baseline. Also disable the screen saver
explicitly — the display-sleep assertion is not documented to suppress it —
and confirm no password-on-wake lock can interrupt the run.

Prefer AC power. On battery, `-s` is silently ignored, Low Power Mode alters
timer coalescing, and the scheduler leans harder on efficiency cores.

## Warm-Up Caveat

Drift correction does not engage until `drift_min_samples` and
`drift_min_span` are both satisfied — about four minutes at defaults. The
measured bias during that window is about -0.94 ms relative to steady state.

If you intend to run without a photocell and rely on a previously
characterized constant offset, collect drift samples during setup or
instructions so the model is live before your first trial.

---

# Drift Correction

Client-side drift correction is enabled by default.

The corrector does not send repeated ECI `NTPClockSync` commands. It queries
the amplifier / Net Station NTP server, records the offset, fits a line, and
applies that correction inside `getTime()`. This avoids resetting the local
event timestamp epoch during a recording.

## How It Works

Three properties are worth understanding, because they explain the settings.

**Offsets are referenced to the monotonic clock.** `ntplib` reports its offset
against the local *system* clock, but event timestamps ride the *monotonic*
clock. Those two diverge continuously, because the operating system's time
daemon (`timed` on macOS, `w32time` on Windows) disciplines the system clock.
Each sample therefore records `sys_mono_skew = local_time - monotonic_time`
and the model fits on `offset_mono = offset + sys_mono_skew`, so OS clock
adjustments cancel algebraically instead of being injected into event
timestamps.

This is not theoretical. In validation, the operating system stepped the
system clock by 256 ms mid-recording; the raw NTP offset moved by the same
amount in the opposite direction, and the monotonic-frame offset changed by
0.15 ms. Event timing was unaffected.

**The correction is closed-loop.** When a new fit is accepted, the model
anchors on the current corrected offset — so timestamps never step — but it
also records the difference between that anchor and the level the new fit
actually measures, and retires that difference at a bounded rate. Without this
the correction would be an open-loop integral of noisy slope estimates and
would random-walk away from truth over a long recording.

**Stale models stop extrapolating.** If fits are being rejected, the last
accepted slope is extrapolated only up to `drift_max_model_age`, after which
the correction holds its value rather than running away.

## Recommended Pattern

```python
from egi_pynetstation import NetStation

ns = NetStation('10.10.10.42', 55513)
ns.connect(
    ntp_ip='10.10.10.51',
    drift_correction=True,       # default
    drift_min_samples=13,        # default
    drift_min_span=180.0,        # default seconds
    drift_max_delay=0.010,       # default seconds
    drift_max_residual=0.003,    # default seconds, +/-3 ms fit residual
    drift_window_minutes=15.0,   # default: local rolling fit
    drift_samples=4,             # default NTP queries per sample
    drift_sample_spacing=0.05,   # default seconds between queries
    drift_slew=0.0002,           # default seconds of correction per second
    drift_max_model_age=600.0,   # default seconds before the model holds
)

ns.begin_rec()

try:
    for trial in range(100):
        # Present stimulus here.
        ns.send_event(event_type='stm+')

        # Sample during safe periods such as fixation or inter-trial
        # intervals. This is an NTP query only, not an ECI clock sync.
        if trial % 10 == 0:
            ns.sample_drift()
finally:
    ns.end_rec()
    ns.disconnect()
```

## Disabling Drift Correction

```python
ns.connect(ntp_ip='10.10.10.51', drift_correction=False)
```

Or toggle it after connecting:

```python
ns.set_drift_correction(False)
ns.set_drift_correction(True)
```

## Tuning the Model

Correction is gated so early NTP noise cannot produce a bad extrapolation. By
default it is not applied until there are at least 13 good samples spanning at
least 180 seconds.

```python
ns.set_drift_requirements(min_samples=13, min_span=180.0)
```

The model rejects high-delay NTP samples and poor-quality fits. By default it
fits a rolling window and applies the accepted model continuously:

```python
ns.set_drift_model_options(
    max_delay=0.010,      # reject NTP samples with >10 ms round-trip delay
    max_residual=0.003,   # reject fits outside +/-3 ms residual
    window_minutes=15.0,  # fit using the last 15 minutes of valid samples
)
```

For a 250 Hz recording, one sample is 4 ms. To relax the fit-quality gate to
that one-sample budget, use `max_residual=0.004`.

**Prefer the rolling window over a cumulative fit.** Setting
`window_minutes=0.0` fits all valid samples, which sounds more stable but
tracks a curving offset series more poorly — an old sample is evidence about
an old clock rate. The 15-minute default is the validated configuration.

Stability controls:

```python
ns.set_drift_stability(
    slew=0.0002,          # max seconds of level correction per second elapsed
    max_model_age=600.0,  # stop extrapolating a fit after this many seconds
)
```

`slew` bounds how fast an outstanding level error is retired, so accepting a
new fit never steps event timestamps. Use `0` to apply level corrections
instantly. `max_model_age=0` extrapolates without bound.

Burst sampling:

```python
ns.set_drift_sampling(samples=4, spacing=0.05)
```

Force a refit from current samples without querying NTP or sending any ECI
command:

```python
estimate = ns.refresh_drift_model()
```

## Inspecting State

```python
print(ns.drift_history())    # every sample collected, including rejected ones
print(ns.drift_estimate())   # current fit and prediction
print(ns.clock_state())      # flat summary, convenient for logging
```

The slope is in seconds of NTP offset change per second of local elapsed
time. To convert to milliseconds per hour:

```python
ms_per_hour = ns.drift_estimate()['slope'] * 1000 * 3600
```

Fields worth logging per trial, and what healthy values look like:

| Field | Healthy | Meaning |
| --- | --- | --- |
| `drift_rejected_fits` | flat after startup | Climbing means the offset series has a discontinuity or is too noisy for `drift_max_residual`. |
| `drift_pending_error` | near zero | Outstanding level error the closed loop is still retiring. |
| `drift_model_age` | near zero | Seconds since the active fit was anchored. Growing means fits are being rejected. |
| `active_drift_slope` | stable | Jumping between values suggests a clock discontinuity. |
| `sys_mono_skew` | may move freely | OS clock discipline. Movement here is expected and should *not* affect timestamps. |
| `drift_model_samples` | at or near the window capacity | Falling means samples are being rejected for delay. |
| `drift_last_reject_reason` | `None` | `too_few_samples`, `short_span`, `degenerate_span`, or `high_residual`. |

All samples remain in `drift_history()` for auditing, but high-delay samples
and samples older than the rolling window are excluded from the active fit.

---

# Important Timing Notes

Use one ECI `NTPClockSync` at the beginning of a normal recording. In this
package, `ntpsync()` is called by `begin_rec()` when an NTP server is
configured.

Do not repeatedly call `ntpsync()` during a recording for drift correction.
Repeated ECI clock syncs can reset the local event timestamp epoch and create
discontinuities in the timestamps sent to Net Station.

`NTPReturnClock` / `sync_return_clock()` is diagnostic. On tested systems its
timestamp response may be delayed until a following command, and follow-up ECI
events can appear in the recording. It is useful for investigating server
clock-start behavior, but it is not the recommended production path.

For visual experiments, capture event timestamps at the actual display flip.
See the integration section above.

The constant marker-to-photocell offset — about 65 ms on the reference setup —
is fixed hardware latency: GPU and display pipeline, photocell response, and
amplifier filtering and sampling. It is not clock-related, does not transfer
between machines, and should be re-characterized whenever the display mode,
refresh rate, stimulus screen position, amplifier sampling rate, or Net Station
filter settings change.

---

# PsychoPy Photocell Test

This repository includes a timing validation script:

```bash
caffeinate -dis python psychopy_photocell_drift.py amp \
  --fullscreen \
  --screen 1 \
  --duration 3600 \
  --sample-interval 15 \
  --drift-min-samples 13 \
  --drift-min-span 180 \
  --drift-max-delay 0.010 \
  --drift-max-residual 0.003 \
  --drift-window-minutes 15 \
  --log photocell_drift.csv \
  --error-log photocell_drift_errors.jsonl
```

The script shows a black screen with a white dot and sends `stm+` on each dot
onset. It captures the flip timestamp in the flip callback, converts and sends
on a worker thread, samples NTP drift during safe inter-trial intervals, and
writes a CSV containing PsychoPy flip times, package event times, send results,
NTP samples, and the full drift-model state per trial.

Use the exported Net Station EVT file and a photocell channel to check whether
the `stm+` marker-to-photocell offset stays stable across the run.

Drift-related options beyond the defaults:

```text
--drift-samples N            NTP queries per drift sample (default 4)
--drift-sample-spacing S     seconds between queries in a burst (default 0.05)
--drift-min-pause S          minimum ITI required to take a sample (default 0.35)
--drift-slew R               max level-correction rate (default 0.0002)
--drift-max-model-age S      stop extrapolating after this age (default 600)
--no-drift-correction        disable correction entirely
```

## What To Check In The Output

1. **Steady-state offset mean and standard deviation**, excluding the first
   five minutes. The warm-up period has no correction applied and is
   systematically different.
2. **Linear trend across the run.** This should be near zero. A persistent
   slope means the correction is not tracking.
3. **`drift_rejected_fits`.** Should be flat after startup.
4. **`sys_mono_skew_ms` versus `drift_correction_ms`.** If the OS moves the
   system clock and the correction passes through without a kink, the
   monotonic-frame referencing is working. An OS clock adjustment during the
   run is a useful test, not a problem.

## Running From the PsychoPy GUI

For Windows users, or anyone who prefers launching from PsychoPy
Coder/Runner:

```text
psychopy_photocell_drift_gui.py
```

Open that file in PsychoPy and press Run. A startup dialog asks for the same
settings as the command-line script.

For a typical amplifier run:

```text
Mode: amp
Net Station IP: leave blank for 10.10.10.42
NTP / amp IP: leave blank for 10.10.10.51
ECI port: leave blank for 55513
Fullscreen: checked
Screen index: 0 or 1, depending on the stimulus display
CSV log path: choose a writable path
Error log path: choose a writable path
NTP sample interval (s): 15
Drift min samples: 13
Drift min span (s): 180
Reject NTP delay above (s): 0.010
Reject drift fit residual above (s): 0.003
Keep last N minutes (0 = all): 15
```

Use `custom` mode if the Net Station IP, NTP IP, or ECI port differ from the
default amp setup.

## Diagnostic Comparison Runs

The script can intentionally send repeated ECI `ntpsync()` commands before or
after stimuli. This is diagnostic only and is not a recommended production
timing mode:

```bash
python psychopy_photocell_drift.py amp --fullscreen --ntpsync-every 5 \
  --log photocell_ntpsync_every5.csv
```

Use `--ntpsync-every 1` to sync before every dot, or `--ntpsync-after-every N`
to sync after stimuli instead. The CSV records whether each stimulus had a
sync immediately before or after it.

---

# Diagnostics

`example2.py` is an interactive ECI command sender and experiment runner. It
prints sent bytes, raw responses, parsed responses, drift samples, and clock
state.

Run a scripted diagnostic:

```bash
python example2.py amp \
  --experiment experiments/experiment_24.txt \
  --transcript exp24.txt \
  --error-log error24.jsonl \
  --no-interactive
```

Useful `example2.py` experiment commands:

```text
ntpsync
sample_drift
drift_report
drift_refit
drift_window 13 180
drift_model 0.010 0.003 15
drift_on
drift_off
event_code stm+
clock_state
```

To disable drift correction in diagnostics:

```bash
python example2.py amp --no-drift-correction
```

---

# Settings Reference

These settings control the client-side drift corrector. They do not change the
amplifier sampling rate or the EEG recording sample rate. Drift sampling
happens only when user code calls `ns.sample_drift()` or
`ns.sample_drift_if_due()`.

| Package setting | Default | Meaning |
| --- | ---: | --- |
| `drift_correction` | `True` | Enables drift-corrected `getTime()`. When disabled, timestamps use the initial sync baseline. |
| `drift_min_samples` | `13` | Minimum valid NTP samples required before correction is applied. |
| `drift_min_span` | `180.0` s | Minimum elapsed time covered by valid samples before correction is applied. |
| `drift_max_delay` | `0.010` s | Rejects NTP samples with higher round-trip delay. Rejected samples stay in `drift_history()` but do not affect the model. |
| `drift_max_residual` | `0.003` s | Rejects fitted lines whose maximum absolute residual exceeds this. Use `0.004` for a one-sample gate at 250 Hz. |
| `drift_window_minutes` | `15.0` min | Fits using only this many recent minutes. Set `0.0` to use all valid samples. |
| `drift_samples` | `4` | NTP queries per drift sample; the lowest-delay reply is kept. |
| `drift_sample_spacing` | `0.05` s | Seconds between queries within one burst. |
| `drift_slew` | `0.0002` | Maximum seconds of level correction retired per second elapsed. `0` applies instantly. |
| `drift_max_model_age` | `600.0` s | Stop extrapolating a fitted slope after this age; the correction then holds. `0` is unbounded. |

Auto-drift scheduling:

| Setting | Default | Meaning |
| --- | ---: | --- |
| `enabled` | `False` | Whether `sample_drift_if_due()` may sample at all. |
| `interval` | `60.0` s | Target seconds between drift samples. |
| `min_pause` | `0.5` s | Minimum idle time an experiment must offer before a sample is taken. |

## API Summary

```python
# Connection
ns.connect(ntp_ip=..., drift_correction=True, ...)
ns.begin_rec()
ns.end_rec()
ns.disconnect()

# Events
ns.send_event(start='now', event_type='stm+', label='stm+')
ns.getTime()                          # timestamp for right now
ns.time_at_monotonic(monotonic_time)  # timestamp for a captured reading

# Drift sampling
ns.sample_drift(samples=None, spacing=None)
ns.sample_drift_if_due(available_pause=None)
ns.configure_auto_drift(enabled=True, interval=60.0, min_pause=0.5)
ns.set_drift_sampling(samples=4, spacing=0.05)

# Drift model
ns.set_drift_correction(True)
ns.set_drift_requirements(min_samples=13, min_span=180.0)
ns.set_drift_model_options(max_delay=0.010, max_residual=0.003,
                           window_minutes=15.0)
ns.set_drift_stability(slew=0.0002, max_model_age=600.0)
ns.refresh_drift_model()

# Inspection
ns.drift_history()
ns.drift_estimate()
ns.clock_state()
```
