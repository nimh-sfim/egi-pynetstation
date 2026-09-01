# EGI PyNetStation

`egi-pynetstation` sends event markers from Python to EGI Net Station / Amp
Server Pro through the Experimental Control Interface (ECI).

## Install

```bash
pip install egi-pynetstation
```

For a development checkout:

```bash
git clone https://github.com/nimh-sfim/egi-pynetstation.git
cd egi-pynetstation
pip install -e .
```

## The five commands

```python
from egi_pynetstation import NetStation

ns = NetStation('10.10.10.42', 55513)

ns.connect(ntp_ip='10.10.10.51')
ns.begin_rec()
ns.send_event(event_type='stim')
ns.end_rec()
ns.disconnect()
```

That is the complete lifecycle for most experiments:

1. `connect()` opens ECI and starts background clock sampling.
2. `begin_rec()` starts a recording and synchronizes its timestamp epoch.
3. `send_event()` timestamps and queues an event marker.
4. `end_rec()` flushes queued events and stops the recording.
5. `disconnect()` closes ECI and its background workers.

## Timing validation

Run a photocell timing test once for every experiment and physical setup:

```bash
python example5_psychopy_photocell_drift.py amp --fullscreen \
  --log timing.csv --error-log timing_errors.jsonl \
  --frame-interval-log timing_frames.csv
```

Also verify local clock precision:

```bash
python -m egi_pynetstation.check_clocks
```

The ordinary drift model uses at least 13 samples spanning 180 seconds. If
the photocell test shows meaningful startup drift, either connect earlier so
the model can collect evidence before `begin_rec()`, or enable the optional
provisional model:

```python
ns.connect(ntp_ip='10.10.10.51', drift_warmup=True)
```

You can test this with example5:

```python
python example5_psychopy_photocell_drift.py amp \
--sessions 1 --duration 3600 \
--staged-drift --fullscreen \
--log ex5_log_drift_warmup.csv \
--error-log ex5_log_drift_warmup_errors.jsonl \
--frame-interval-log ex5_log_drift_warmup_frames.csv
```

The provisional model can engage after five samples spanning 20 seconds and
permanently hands off to the ordinary model when it is ready. Validate the
choice on the actual stimulus computer, display, network, and amplifier used
for the experiment.

See the [Timing Test](https://egi-pynetstation.readthedocs.io/en/latest/timing_test.html)
and [Advanced guide](https://egi-pynetstation.readthedocs.io/en/latest/advanced.html)
for the full procedure.

## PsychoPy

Mark the flip that presents a stimulus:

```python
stim.draw()
win.callOnFlip(ns.send_event, event_type='stim')
win.flip()
```

`send_event()` captures the time immediately and performs network I/O on a
worker thread, so it is safe to use in a flip callback.

## Documentation

- [Installation](https://egi-pynetstation.readthedocs.io/en/latest/installation.html)
- [Quickstart](https://egi-pynetstation.readthedocs.io/en/latest/quickstart.html)
- [Using PsychoPy](https://egi-pynetstation.readthedocs.io/en/latest/psychopy.html)
- [Timing Test](https://egi-pynetstation.readthedocs.io/en/latest/timing_test.html)
- [Examples](https://egi-pynetstation.readthedocs.io/en/latest/examples.html)
- [Advanced guide](https://egi-pynetstation.readthedocs.io/en/latest/advanced.html)
- [API reference](https://egi-pynetstation.readthedocs.io/en/latest/api.html)

Run the timing test once for every experiment and physical setup before
collecting data. At the end of a validation or production run, inspect
`ns.session_summary()`.

## License

This software is a United States Government Work. See [LICENSE](LICENSE).
