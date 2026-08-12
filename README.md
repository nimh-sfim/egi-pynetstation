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
python -m egi_pynetstation.check_clocks
```

From a repository checkout, `python check_clocks.py` does the same thing.

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

Send markers straight from `win.callOnFlip()`. The package handles the
threading, the timestamp capture, and the flushing. `send_event()` is
non-blocking, so there is nothing to configure.

```python
from psychopy import visual, core
from egi_pynetstation import NetStation

ns = NetStation('10.10.10.42', 55513)
ns.connect(ntp_ip='10.10.10.51', auto_drift_interval=15.0)

win = visual.Window(fullscr=True, screen=1, color='black')
stim = visual.TextStim(win, text='+')

try:
    ns.begin_rec()

    for trial in range(100):
        stim.draw()
        win.callOnFlip(ns.send_event, event_type='stm+', label='stimulus')
        win.flip()
        core.wait(0.5)

        # Inter-trial interval. Tell the package how much idle time it may
        # use; it samples only when one is due and there is room for it.
        ns.sample_drift_if_due(available_pause=1.0)
        core.wait(1.0)
finally:
    ns.end_rec()      # flushes any queued events
    ns.disconnect()
```

That is the whole integration. No `queue`, no `threading`, no manual
timestamp bookkeeping.

## How Event Sending Works

`send_event()` captures `time.monotonic()` on the calling thread — the one
aligned with your stimulus — puts the event on a queue, and returns. A
background worker converts that reading into a drift-corrected event
timestamp and writes to the socket.

This makes `send_event()` safe to call from a flip callback. Measured block
time on the calling thread is about 16 microseconds, versus milliseconds if
the socket write happened inline.

The timestamp describes **when the flip happened**, not when the network
write completed, so a slow or backlogged send cannot move your event
timing.

Two consequences worth knowing:

- `send_event()` returns `None`, because there is no response yet. Use
  `wait=True` on any individual call if you need the ECI response.
- Send failures cannot raise into your experiment code, so they are
  collected instead. Check them at the end of a run:

```python
errors = ns.event_errors()
if errors:
    print(f'{len(errors)} events failed to send:', errors[:3])
```

Queued events are flushed automatically by `end_rec()` and `disconnect()`,
and again at interpreter exit as a safety net, so a script that crashes or
forgets to disconnect still delivers what it queued. Call
`ns.flush_events()` yourself only if you need a synchronisation point
mid-experiment.

## Mixing Flip Markers and Ordinary Markers

There is one setting, and you rarely need it. `send_event()` never blocks;
`send_event(wait=True)` blocks and returns the parsed ECI response.

That means both usages are the same call and can be mixed freely:

```python
# visual onset: timestamped at the flip
win.callOnFlip(ns.send_event, event_type='stm+')
win.flip()

# response marker: timestamped at this line
ns.send_event(event_type='resp', desc=f'key={key}')
```

Neither blocks, both capture their timestamp on the calling thread, and
order is preserved. The only difference is *which moment* each one marks —
the flip, or the line of code. Use `callOnFlip` when the marker must line
up with what the participant saw; call it directly for anything else.

Pass `wait=True` when you actually need the amplifier's reply — a
diagnostic console, or checking that a command was accepted. Do **not** use
it inside a flip callback: on the reference setup a non-blocking send takes
about 56 us and a blocking one 7.6 ms, and the blocking version pushes the
following frame.

## Drift Sampling

`drift_correction` and `auto_drift` sound alike but do different jobs. They
are the two halves of one pipeline — **collect, then apply** — and
correction only happens when both are in play:

- **`auto_drift` is the producer.** It governs whether NTP drift samples are
  collected, and on what schedule. No samples, no model.
- **`drift_correction` is the consumer.** It governs only whether
  `getTime()` applies the fitted model to the timestamp it returns. Off, and
  `getTime()` returns raw elapsed time.

| `auto_drift` | `drift_correction` | Result |
|---|---|---|
| on | on | **The default, and the working configuration.** |
| on | off | Samples collected and logged, but `getTime()` ignores them. Useful for measuring drift without correcting for it. |
| off | on | No samples collected, so correction never engages. An explicit `sample_drift()` still works. |
| off | off | No drift machinery at all. |

Both default to `True`. What remains yours to arrange is *who takes the
samples*.

Drift samples are NTP queries. They do **not** send ECI clock-sync commands
and do not create markers. They do block the calling thread for roughly
170 ms at default settings, so they must not land near a flip.

**Auto-drift is a schedule, not a worker — nothing polls it.** The schedule
is enabled by default, but `sample_drift_if_due()` is the only thing that
acts on it, so an experiment that never calls it collects no samples at all
and drift correction never engages. There are two ways to arrange the
sampling.

**Cooperative (default).** The package owns the schedule, your experiment
owns the timing-safety window:

```python
ns.connect(ntp_ip=..., auto_drift_interval=15.0,
           auto_drift_min_pause=0.35)
ns.sample_drift_if_due(available_pause=iti_remaining)   # in your ITI
```

**Background.** The package samples on its own, no cooperation needed:

```python
ns.connect(ntp_ip=..., auto_drift_background=True)
```

Either can also be set after connecting, or changed mid-session, with
`ns.configure_auto_drift(enabled=True, interval=15.0, background=False)`.

The NTP query runs outside every lock, so a background sample cannot block
`getTime()` by more than a few microseconds. What it *can* do is put a
network wakeup near a screen flip. Prefer the cooperative form for visual
experiments with usable inter-trial intervals, and background for anything
else — especially long runs, where a forgotten call would quietly cost you
drift correction for the whole session.

If auto-drift is enabled, sampling is cooperative, and far fewer samples
were collected than the interval implies, `disconnect()` logs a warning and
writes a `drift_undersampled` record. That is the safety net, not a
substitute for arranging the sampling.

`available_pause` is how much idle time you can safely give up. If a sample
is not due yet, or the pause you offered is too short, the call returns
without sampling. Omit `available_pause` if your intervals are comfortably
long and you do not want the check.

Return values:

```python
{'sampled': True,  'reason': 'due', 'sample': {...}}
{'sampled': False, 'reason': 'not_due', 'seconds_until_due': 12.4}
{'sampled': False, 'reason': 'pause_too_short', 'min_pause': 0.35}
{'sampled': False, 'reason': 'disabled'}
{'sampled': False, 'reason': 'not_synced'}
```

To manage the schedule yourself, call `ns.sample_drift()` from a point you
know is safe.

Each call makes several rapid NTP queries and keeps the lowest-delay reply.
NTP offset error tracks path asymmetry, which tracks round-trip delay, so
the fastest reply in a short burst is the most trustworthy. Selecting the
minimum beats averaging, which folds the bad replies back in.

```python
ns.set_drift_sampling(samples=4, spacing=0.05)   # defaults
```

A burst blocks for about `(samples - 1) * spacing` plus the round trips.
Budget for that when choosing `min_pause`.

**How often?** The model needs `drift_min_samples` valid samples spanning
`drift_min_span` seconds before it engages — about four minutes at the
defaults, which sample every 15 seconds. Every 15 to 60 seconds is
reasonable; more frequent sampling mostly reduces noise on the slope
estimate.

## Prevent the Machine From Sleeping

On macOS, wrap your run:

```bash
caffeinate -dis python my_experiment.py
```

`-d` prevents display sleep, `-i` prevents idle sleep, `-s` prevents system
sleep (honored only on AC power). Given a utility, the assertions are held
for exactly that process's lifetime.

This matters for more than the screensaver. Python's `time.monotonic()` on
macOS does not advance while the machine is asleep, so a sleep mid-recording
would corrupt the elapsed-time baseline. Also disable the screen saver
explicitly — the display-sleep assertion is not documented to suppress it —
and make sure no password-on-wake lock can interrupt the run.

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

# Example Scripts

| Script | What it is |
| --- | --- |
| [`example3_stroop.py`](example3_stroop.py) | A complete Stroop task. **Start here** if you are writing an experiment. |
| [`example4_psychopydemo.py`](example4_psychopydemo.py) | The smallest possible PsychoPy loop, in the style of the bundled PsychoPy hardware demos. |
| [`example.py`](example.py) | Non-visual: connect, send timed events, disconnect. No PsychoPy needed. |
| [`example2.py`](example2.py) | Interactive ECI console and scripted diagnostics. See [Diagnostics](#diagnostics). |
| [`example5_psychopy_photocell_drift.py`](example5_psychopy_photocell_drift.py) | Timing validation against a photocell. See [PsychoPy Photocell Test](#psychopy-photocell-test). |

## Stroop Example

`example3_stroop.py` runs five colour words in five ink colours — all 25
combinations shuffled, so 5 congruent and 20 incongruent trials. It is
written as a reference for the smallest clean setup: every line that talks
to the amplifier is marked with an `EGI:` comment, and there are only seven
of them.

```bash
python example3_stroop.py
```

Edit the three addresses at the top of the file for your network.

Markers written to the recording:

| Event type | When | Description field |
| --- | --- | --- |
| `cong` | stimulus onset, word matches ink | — |
| `inco` | stimulus onset, word differs from ink | — |
| `resp` | button press | `key=r incorrect target=p` |
| `miss` | no response before the timeout | `no response within 2.000 s` |

The response marker shows the convention worth copying: put the
human-readable outcome in `desc`, where it is legible in Net Station
without decoding anything, and keep the machine-readable version in `data`
for analysis.

```python
ns.send_event(
    event_type='resp',
    label=f'key {pressed}',
    desc=f'key={pressed} {"correct" if is_correct else "incorrect"} '
         f'target={correct_key}',
    data={'trl_': index, 'key_': pressed, 'corr': is_correct, 'rt__': rt},
)
```

It also demonstrates the two ECI constraints that catch people out:
**event types must be exactly four ASCII characters**, and **every key in
`data` must be exactly four characters too**. Hence `trl_`, `key_`, `corr`,
`rt__`, `word`, `colr`. Values may be `bool`, `int`, `float`, or `str`, and
the dictionary must be flat. `label` and `desc` are free text up to 256
characters.

Marking a `miss` matters more than it looks: without it, a timed-out trial
has a stimulus onset with no matching response event, which makes epoching
awkward later.

Note that a 25-trial run takes roughly two minutes, so drift correction
will probably never engage — it needs 13 NTP samples spanning 180 seconds
by default. Timestamps are still correct, just uncorrected for drift. See
[Warm-Up Caveat](#warm-up-caveat).

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

This stops `getTime()` from applying the model. Sampling continues, so the
drift is still measured and logged — you just aren't correcting for it.

```python
ns.connect(ntp_ip='10.10.10.51', drift_correction=False)
```

To stop the sampling as well, disable the producer too:

```python
ns.connect(ntp_ip='10.10.10.51', drift_correction=False, auto_drift=False)
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
    stall_after=5,        # consecutive rejected fits before logging a stall
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
caffeinate -dis python example5_psychopy_photocell_drift.py amp \
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

Press **q** or **escape** at any point to stop the run early. The CSV log is
still written, and the recording is closed cleanly.

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
example5_psychopy_photocell_drift_gui.py
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
python example5_psychopy_photocell_drift.py amp --fullscreen --ntpsync-every 5 \
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

# Error Log

Pass `error_log` when constructing the station to get a JSON-lines record
of anything that goes wrong, for reading after a session:

```python
ns = NetStation('10.10.10.42', 55513, error_log='session_errors.jsonl')
```

It can also be set or changed later with `ns.set_error_log(path)`. Parent
directories are created for you. If the path turns out to be unwritable the
failure is logged through Python's `logging` and the recording continues —
losing the log is bad, losing the recording is worse.

Three kinds of record are written, each tagged with a `record` field:

| `record` | Written when |
| --- | --- |
| `session_start` | `connect()` succeeds. Captures the drift settings in force, so a log file says what produced it. |
| `eci_response_error` | The amplifier returned something unparseable or an error. Includes `cmd`, the raw bytes as `raw_hex` and a printable `raw_display`, and — for events — `event_type`, `label`, and `start`. |
| `event_send_failure` | An asynchronous send raised. Includes `event_type`, `label`, and `start`. |
| `drift_model_engaged` | Drift correction became active for the first time. Records the fitted slope, sample count, and span. |
| `drift_model_stalled` | Fits have been rejected `stall_after` times in a row (default 5) after the model had engaged. |
| `drift_model_recovered` | A fit was accepted again. Records `stall_duration` and `rejected_during_stall`. |
| `drift_undersampled` | Auto-drift was enabled but almost nothing was sampled — usually a missing `sample_drift_if_due()` call. |

The drift records close a real blind spot. A stalled model is silent by
construction: fits are refused, the last accepted slope keeps being
extrapolated, and nothing raises until the timing error has already grown.
In one hour-long validation run this went unnoticed for 15 minutes and cost
about 17 ms. Now it is one line while it is happening:

```json
{"record": "drift_model_stalled", "elapsed": 330.0,
 "drift_consecutive_rejections": 3, "drift_last_reject_reason": "high_residual",
 "drift_model_age": 45.0, "extrapolation_frozen": false,
 "active_slope_ms_per_hour": 36.0}
```

Startup rejections — too few samples, span too short — are normal and are
never reported as a stall. Tune the threshold with
`ns.set_drift_stability(stall_after=5)`, and check `drift_stalled` or
`drift_consecutive_rejections` in `clock_state()` at runtime.

`event_send_failure` matters most in practice. Asynchronous sending is the
default, and an async failure cannot be raised into your experiment code,
so the log file is where you find out it happened. The same records are
available at runtime from `ns.event_errors()`.

Every failure record carries a **`clock` snapshot** — the full
`clock_state()` at the moment of the error. That is usually what explains
it: whether the drift model was stale, how long fits had been rejected,
what the system-versus-monotonic skew was doing. Reading a log is much
faster than reproducing the failure.

```python
import json

for line in open('session_errors.jsonl'):
    r = json.loads(line)
    if r['record'] == 'event_send_failure':
        print(r['event_type'], r['error'],
              'model age:', r['clock']['drift_model_age'],
              'rejected fits:', r['clock']['drift_rejected_fits'])
```

Note that an unparseable response is *reported*, not raised: `send_event()`
with `wait=True` returns a diagnostic dictionary with `ok: False` rather
than throwing, so a single bad reply cannot end a recording.

---

# Settings Reference

These settings control the client-side drift corrector. They do not change the
amplifier sampling rate or the EEG recording sample rate. Drift sampling
happens only when user code calls `ns.sample_drift()` or
`ns.sample_drift_if_due()`.

| Package setting | Default | Meaning |
| --- | ---: | --- |
| `drift_correction` | `True` | Applies the fitted model in `getTime()`. When disabled, timestamps use the initial sync baseline; sampling continues. |
| `drift_min_samples` | `13` | Minimum valid NTP samples required before correction is applied. |
| `drift_min_span` | `180.0` s | Minimum elapsed time covered by valid samples before correction is applied. |
| `drift_max_delay` | `0.010` s | Rejects NTP samples with higher round-trip delay. Rejected samples stay in `drift_history()` but do not affect the model. |
| `drift_max_residual` | `0.003` s | Rejects fitted lines whose maximum absolute residual exceeds this. Use `0.004` for a one-sample gate at 250 Hz. |
| `drift_window_minutes` | `15.0` min | Fits using only this many recent minutes. Set `0.0` to use all valid samples. |
| `drift_samples` | `4` | NTP queries per drift sample; the lowest-delay reply is kept. |
| `drift_sample_spacing` | `0.05` s | Seconds between queries within one burst. |
| `drift_slew` | `0.0002` | Maximum seconds of level correction retired per second elapsed. `0` applies instantly. |
| `drift_max_model_age` | `600.0` s | Stop extrapolating a fitted slope after this age; the correction then holds. `0` is unbounded. |
| `auto_drift` | `True` | Enable the drift sampling schedule. Pass `False` to disable. |
| `auto_drift_interval` | `60.0` s | Target seconds between drift samples. |
| `auto_drift_min_pause` | `0.5` s | Minimum idle time before a cooperative sample is taken. |
| `auto_drift_background` | `False` | Sample from a package-owned thread rather than requiring `sample_drift_if_due()`. |

Auto-drift scheduling:

| Setting | Default | Meaning |
| --- | ---: | --- |
| `enabled` | `False` | Whether `sample_drift_if_due()` may sample at all. |
| `interval` | `60.0` s | Target seconds between drift samples. |
| `min_pause` | `0.5` s | Minimum idle time an experiment must offer before a sample is taken. Cooperative sampling only. |
| `background` | `False` | Sample from a package-owned thread instead of requiring `sample_drift_if_due()` calls. |

## API Summary

```python
# Connection
ns.connect(ntp_ip=..., drift_correction=True, auto_drift=True, ...)
ns.begin_rec()
ns.end_rec()          # flushes queued events
ns.disconnect()       # flushes queued events, stops the sender

# Events
ns.send_event(start='now', event_type='stm+', label='stm+')
ns.send_event(..., wait=True)         # block and return the ECI response
ns.flush_events(timeout=None)         # block until the queue drains
ns.event_errors()                     # failures from asynchronous sends
ns.getTime()                          # timestamp for right now
ns.time_at_monotonic(monotonic_time)  # timestamp for a captured reading

# Drift sampling
ns.sample_drift(samples=None, spacing=None)
ns.sample_drift_if_due(available_pause=None)
ns.configure_auto_drift(enabled=True, interval=60.0, min_pause=0.5,
                        background=False)
ns.set_drift_sampling(samples=4, spacing=0.05)

# Drift model
ns.set_drift_correction(True)
ns.set_drift_requirements(min_samples=13, min_span=180.0)
ns.set_drift_model_options(max_delay=0.010, max_residual=0.003,
                           window_minutes=15.0)
ns.set_drift_stability(slew=0.0002, max_model_age=600.0, stall_after=5)
ns.refresh_drift_model()

# Inspection
ns.drift_history()
ns.drift_estimate()
ns.clock_state()
```

## License

This software is a United States Government Work. It was developed by NIH
employees as part of their official duties and, under 17 U.S.C. § 105, is in
the public domain within the United States. Outside the United States, and for
contributions by non-Government authors, it is licensed under the MIT License.
See [LICENSE](LICENSE) for the full text.

Attribution cannot be required of a public domain work, so this is a request
rather than a condition — but it is a sincere one. If `egi-pynetstation`
supports your research, please cite it and acknowledge the authors and the NIH
Center for Multimodal Neuroimaging ([CMN](https://cmn.nimh.nih.gov)) in any
derived software or publications.
