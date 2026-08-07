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
    drift_correction=True,   # default
    drift_min_samples=4,     # default
    drift_min_span=90.0,     # default seconds
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

### Setting the Drift Window

The correction is intentionally gated so early NTP noise does not produce a
bad extrapolation. By default, correction is not applied until there are at
least 4 samples spanning at least 90 seconds.

```python
ns.set_drift_requirements(min_samples=4, min_span=90.0)
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
  --log /Volumes/PJM/logs/photocell_drift.csv \
  --error-log /Volumes/PJM/logs/photocell_drift_errors.jsonl
```

The script shows a black screen with a white dot and sends `stm+` every time
the dot appears. It enables drift correction, samples NTP drift during black
intervals, and writes a CSV log containing PsychoPy flip times, package event
times, send results, and NTP offset samples.

Use the exported Net Station EVT file and a photocell channel to check whether
the `stm+` marker-to-photocell offset stays stable across the run.

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
drift_window 4 90
drift_on
drift_off
event_code stm+
clock_state
```

To disable drift correction in diagnostics:

```bash
python example2.py amp --no-drift-correction
```
