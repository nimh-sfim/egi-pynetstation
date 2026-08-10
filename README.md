# EGI PyNetStation

## About

`egi-pynetstation` is a Python interface for sending ECI commands and event
markers to EGI Net Station / Amp Server Pro.

The package supports NTP-based event timing. A single ECI `NTPClockSync`
establishes the event timestamp epoch, and optional client-side drift
correction can then compensate for slow clock drift between the stimulus
computer and the Net Station / amplifier NTP server.

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

You can also run scripts from the repository folder with the repository on
`PYTHONPATH`, but editable install is usually less surprising.

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

## Drift Correction

Client-side drift correction is enabled by default.

The drift corrector does not send repeated ECI `NTPClockSync` commands. It
only queries the amplifier / Net Station NTP server and records the estimated
NTP offset. Once enough samples have been collected over a long enough window,
`getTime()` applies a linear correction to event timestamps before events are
sent.

This avoids resetting the local event timestamp epoch during a recording.

### Recommended Pattern

```python
from egi_pynetstation import NetStation

ns = NetStation('10.10.10.42', 55513)
ns.connect(
    ntp_ip='10.10.10.51',
    drift_correction=True,       # default
    drift_min_samples=13,        # default
    drift_min_span=180.0,        # default seconds
    drift_max_delay=0.010,       # default seconds
    drift_max_residual=0.003,    # default seconds
    drift_window_minutes=15.0,   # default: local rolling fit
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

### Disabling Drift Correction

To force drift correction off:

```python
ns.connect(ntp_ip='10.10.10.51', drift_correction=False)
```

You can also toggle it after connecting:

```python
ns.set_drift_correction(False)
ns.set_drift_correction(True)
```

### Setting the Drift Model

The correction is intentionally gated so early NTP noise does not produce a
bad extrapolation. By default, correction is not applied until there are at
least 13 good samples spanning at least 180 seconds.

```python
ns.set_drift_requirements(min_samples=13, min_span=180.0)
```

The model rejects high-delay NTP samples. By default it fits a local rolling
window and applies that model continuously: when a new fit is accepted, the
timestamp baseline is anchored at the current corrected offset, so `getTime()`
may change correction slope but not jump event timestamps. Use `0` only when
you deliberately want one cumulative fit over all valid samples:

```python
ns.set_drift_model_options(
    max_delay=0.010,      # reject NTP samples with >10 ms round-trip delay
    max_residual=0.003,   # reject fits with >3 ms residual
    window_minutes=15.0,  # fit using the last 15 minutes of valid samples
)
```

Inspect the estimate:

```python
print(ns.drift_history())
print(ns.drift_estimate())
print(ns.clock_state())
```

The drift estimate slope is in seconds of NTP offset change per second of
local elapsed time. To convert to milliseconds per hour:

```python
estimate = ns.drift_estimate()
ms_per_hour = estimate['slope'] * 1000 * 3600
```

The cache model is simple: the package stores the most recent fitted line
(`slope`, `intercept`, sample count, and model span) and reuses it for
`getTime()` calls. The line is recomputed only when a new NTP drift sample is
collected or when drift settings change. All samples remain in
`drift_history()` for auditing, but high-delay samples and samples older than
the rolling window are excluded from the active fit. Set
`window_minutes=0.0` to disable the rolling window and use all valid samples.

## Important Timing Notes

Use one ECI `NTPClockSync` at the beginning of a normal recording. In this
package, `ntpsync()` is called by `begin_rec()` when an NTP server is
configured.

Do not repeatedly call `ntpsync()` during a recording for drift correction.
Repeated ECI clock syncs can reset the local event timestamp epoch and create
discontinuities in the timestamps sent to Net Station.

`NTPReturnClock` / `sync_return_clock()` is diagnostic. On tested systems, its
timestamp response may be delayed until a following command, and follow-up ECI
events can appear in the recording. It is useful for investigating the server
clock-start behavior, but it is not the recommended production drift-correction
path.

For visual experiments, capture event timestamps at the actual display flip
when possible. In PsychoPy, use `win.callOnFlip()` and send the event with the
captured `start` value.

## PsychoPy Photocell Test

This repository includes a timing test:

```bash
python psychopy_photocell_drift.py amp \
  --fullscreen \
  --sample-interval 15 \
  --drift-min-samples 13 \
  --drift-min-span 180 \
  --drift-max-delay 0.010 \
  --drift-max-residual 0.003 \
  --drift-window-minutes 15 \
  --log /Volumes/PJM/logs/photocell_drift.csv \
  --error-log /Volumes/PJM/logs/photocell_drift_errors.jsonl
```

The script shows a black screen with a white dot and sends `stm+` every time
the dot appears. It enables drift correction, samples NTP drift during black
intervals, and writes a CSV log containing PsychoPy flip times, package event
times, send results, and NTP offset samples.

Use the exported Net Station EVT file and a photocell channel to check whether
the `stm+` marker-to-photocell offset stays stable across the run.

For PsychoPy experiments that use this package directly, use the same
connection-time drift settings:

```python
from egi_pynetstation import NetStation

ns = NetStation('10.10.10.42', 55513)
ns.connect(
    ntp_ip='10.10.10.51',
    drift_correction=True,
    drift_min_samples=13,
    drift_min_span=180.0,
    drift_max_delay=0.010,
    drift_max_residual=0.003,
    drift_window_minutes=15.0,
)

ns.begin_rec()

# On the actual stimulus flip, capture the package timestamp. Then hand that
# timestamp to a worker thread or queue that sends the ECI event just after the
# frame appears, so the socket write does not delay the flip itself.
def mark_stimulus_onset():
    event_start = ns.getTime()
    event_queue.put(('stm+', event_start))

win.callOnFlip(mark_stimulus_onset)
```

Sample drift during black screens, fixation, or inter-trial intervals:

```python
if time_since_last_sample >= 15.0:
    ns.sample_drift()
```

### Running From the PsychoPy GUI

For Windows users, or anyone who prefers launching from PsychoPy Coder/Runner,
use:

```text
psychopy_photocell_drift_gui.py
```

Open that file in PsychoPy and press Run. A startup dialog will ask for the
same settings as the command-line script.

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

For a longer one-hour validation run:

```bash
python psychopy_photocell_drift.py amp \
  --fullscreen \
  --screen 1 \
  --duration 3600 \
  --sample-interval 15 \
  --drift-min-samples 13 \
  --drift-min-span 180 \
  --drift-max-delay 0.010 \
  --drift-max-residual 0.003 \
  --drift-window-minutes 15 \
  --log /Volumes/PJM/logs/photocell_drift_1hr.csv \
  --error-log /Volumes/PJM/logs/photocell_drift_1hr_errors.jsonl
```

This samples NTP drift every 15 seconds, waits for at least 13 valid samples
over 3 minutes before applying drift correction, rejects NTP samples whose
round-trip delay is above 10 ms, and fits the correction using the last
15 minutes of valid samples.

For comparison runs, the PsychoPy script can intentionally send repeated ECI
`ntpsync()` commands before stimuli. This is diagnostic only and is not the
recommended production timing mode:

```bash
python psychopy_photocell_drift.py amp \
  --fullscreen \
  --ntpsync-every 5 \
  --log /Volumes/PJM/logs/photocell_ntpsync_every5.csv
```

Use `--ntpsync-every 1` to sync before every dot, or omit the option for the
normal drift-corrected path. To sync after stimuli instead:

```bash
python psychopy_photocell_drift.py amp \
  --fullscreen \
  --ntpsync-after-every 5 \
  --log /Volumes/PJM/logs/photocell_ntpsync_after_every5.csv
```

The CSV records whether each stimulus had a sync immediately before it or
after it.

## Diagnostics

`example2.py` is an interactive ECI command sender and experiment runner. It
prints sent bytes, raw responses, parsed responses, drift samples, and clock
state.

Run a scripted diagnostic:

```bash
python example2.py amp \
  --experiment experiments/experiment_24.txt \
  --transcript /Volumes/PJM/logs/exp24.txt \
  --error-log /Volumes/PJM/logs/error24.jsonl \
  --no-interactive
```

Useful `example2.py` experiment commands:

```text
ntpsync
sample_drift
drift_report
drift_window 13 180
drift_model 0.010 15
drift_on
drift_off
event_code stm+
clock_state
```

To disable drift correction in diagnostics:

```bash
python example2.py amp --no-drift-correction
```

## Drift Settings Reference

These settings control the client-side drift corrector. They do not change the
amplifier sampling rate or the EEG recording sample rate. Drift sampling
happens only when user code calls `ns.sample_drift()`.

| Package setting | Default | Meaning |
| --- | ---: | --- |
| `drift_correction` | `True` | Enables drift-corrected `getTime()`. When disabled, timestamps use the initial sync baseline. |
| `drift_min_samples` | `13` | Minimum number of valid NTP samples required before correction is applied. |
| `drift_min_span` | `180.0` seconds | Minimum elapsed time covered by valid samples before correction is applied. |
| `drift_max_delay` | `0.010` seconds | Rejects NTP samples whose round-trip delay is higher than this. Rejected samples stay in `drift_history()` but do not affect the model. |
| `drift_max_residual` | `0.003` seconds | Rejects fitted drift lines whose maximum residual exceeds this threshold. The last active correction continues instead. |
| `drift_window_minutes` | `15.0` minutes | Fits the active correction model using only this many recent minutes. Set `0.0` to use all valid samples. |

Connection options:

```python
ns.connect(
    ntp_ip='10.10.10.51',
    drift_correction=True,
    drift_min_samples=13,
    drift_min_span=180.0,
    drift_max_delay=0.010,
    drift_max_residual=0.003,
    drift_window_minutes=15.0,
)
```

Runtime controls:

```python
ns.set_drift_correction(True)
ns.set_drift_requirements(min_samples=13, min_span=180.0)
ns.set_drift_model_options(
    max_delay=0.010,
    max_residual=0.003,
    window_minutes=15.0,
)
```
