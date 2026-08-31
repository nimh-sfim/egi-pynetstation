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

Event types must be exactly four ASCII characters. Drift correction and
background NTP sampling are enabled automatically.

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

- [Installation](docs/installation.rst)
- [Quickstart](docs/quickstart.rst)
- [Using PsychoPy](docs/psychopy.rst)
- [Timing Test](docs/timing_test.rst)
- [Examples](docs/examples.rst)
- [Advanced guide](docs/advanced.rst)
- [API reference](docs/api.rst)

Run the timing test once for every experiment and physical setup before
collecting data. At the end of a validation or production run, inspect
`ns.session_summary()`.

## License

This software is a United States Government Work. See [LICENSE](LICENSE).
