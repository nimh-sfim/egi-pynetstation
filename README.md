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

## Calculating Drift
NEW to 2.0+: Drift calculations are now done in the background. The timing
model becomes most stable 180 seconds after `connect()` is called, but in
our tests on macOS stimulus computers, no meaningful drift happened before
that point. Some windows machines may have drift exceeding that, so we
recommend running both:
1. Running `example5_psychopy_photocell_drift.py` with default settings
2. Running `check_clocks.py` to see output similar to the following: 

```bash
python   : 3.11.15
platform : macOS-26.6.2-arm64-arm-64bit

time          impl=clock_gettime(CLOCK_REALTIME)
			  claimed=1.000e-06 s  measured=7.153e-07 s  monotonic=False
monotonic     impl=mach_absolute_time()
			  claimed=4.167e-08 s  measured=4.098e-08 s  monotonic=True
perf_counter  impl=mach_absolute_time()
			  claimed=4.167e-08 s  measured=4.098e-08 s  monotonic=True

egi wall      measured=7.153e-07 s
egi monotonic measured=4.098e-08 s

sleep(0.05)  : mean overshoot 4.59 ms, max 5.07 ms
egi wall-monotonic skew jitter over 2000 reads: 0.0014 ms
egi-pynetstation clocks look suitable for drift-corrected ECI timing.
```

So long as the final lines are:
`egi-pynetstation clocks look suitable for drift-corrected ECI timing`
the drift should be stable. 

### What to do if drift isn't stable

If you find the initial drift on `example5_psychopy_photocell_drift.py` is not
stable, then we recommend you use a two-step timing model, already baked into the package.
Simply change your `connect()` line to be:
```python
ns.connect(ntp_ip=amp_ip, drift_warmup=True)
```
This will calculate a drift model on the first 20 seconds after `connect()` is called. 
In our tests, even on intentionally unstable machines, this was enough to adjust for the drift. 
While the initial model should protect you from early drift, the full 180 second model will automatically
kick in when it has enough samples. So there's no downside to using the warmup model. 
As always, verify with a timing test (Example5 and/or your own experiment)!

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
