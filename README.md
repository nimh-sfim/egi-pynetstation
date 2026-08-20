# EGI PyNetStation

## About

`egi-pynetstation` is a Python interface for sending ECI 
(Experimental Control Interface) commands and event markers 
to EGI Net Station / Amp Server Pro.

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

### Byte Order (`endian`)

The default `endian='NTEL'` is correct on both Intel and Apple silicon Macs.
The value is a legacy ECI byte-order token, not a current processor or
operating-system name:

| Token | Byte order | Appropriate systems |
|---|---|---|
| `NTEL` | Little-endian | All current Macs (Intel and Apple silicon), Intel/AMD PCs, and most current ARM64 systems |
| `MAC-` | Big-endian | Legacy PowerPC-era Macs |
| `UNIX` | Big-endian | Legacy big-endian Unix systems |

Do not select `MAC-` merely because the stimulus computer is a Mac. Passing
a big-endian token from a little-endian computer makes the ECI handshake
disagree with the package's native-packed multibyte values. Big-endian hosts
are represented by the protocol but have not been tested with this package.
See [Diagnostics](docs/diagnostics.rst) for the protocol background.

---

# Documentation

Full documentation lives at
**[egi-pynetstation.readthedocs.io](https://egi-pynetstation.readthedocs.io/en/latest/)**.
It is the source of truth for everything below; this README is deliberately
kept to the parts you need before you have read anything else.

| Page | What is there |
| --- | --- |
| [Installation](docs/installation.rst) | PyPI, GitHub, PsychoPy, clock checking, keeping the machine awake |
| [Quickstart](docs/quickstart.rst) | Session shape, event format rules, timestamps, checking the run afterwards |
| [Using PsychoPy](docs/psychopy.rst) | Flip-callback integration, what not to do in a callback, stimulus computer setup |
| [Drift correction](docs/drift.rst) | What it does, how sampling works, every setting and its default |
| [Diagnostics](docs/diagnostics.rst) | Runtime state, the JSON-lines error log, failed ECI responses, byte order |
| [Examples](docs/examples.rst) | Walkthroughs of the scripts below, including the photocell validation |

## Example Scripts

All live in the repository root.

| Script | What it is |
| --- | --- |
| [`example3_stroop.py`](example3_stroop.py) | A complete Stroop task. **Start here** if you are writing an experiment. |
| [`example4_psychopydemo.py`](example4_psychopydemo.py) | The smallest possible PsychoPy loop, in the style of the bundled PsychoPy hardware demos. |
| [`example.py`](example.py) | Non-visual: connect, send timed events, disconnect. No PsychoPy needed. |
| [`example2.py`](example2.py) | Interactive ECI console and scripted diagnostics. |
| [`example5_psychopy_photocell_drift.py`](example5_psychopy_photocell_drift.py) | Timing validation against a photocell. |
| [`example5_psychopy_photocell_drift_gui.py`](example5_psychopy_photocell_drift_gui.py) | The same validation, launched from a PsychoPy dialog. |

---

# Things To Get Right

Short version of the three mistakes that actually cost people data. Each is
explained properly in the docs.

**Drift correction is on by default, and so is sampling for it.** A background
thread takes NTP samples on its own; there is nothing to wire up.
`ns.connect(ntp_ip=...)` is the whole setup. See
[Drift correction](docs/drift.rst).

**Use exactly one ECI clock sync per recording.** `begin_rec()` performs it.
Calling `ntpsync()` again to "keep the clock fresh" re-bases the event
timestamp epoch and is refused unless you pass `force=True` for diagnostics.
One `NetStation` object records once, for the same reason.

**A non-blocking send cannot report its own failure.** `send_event()` returns
`None` immediately — that is what makes it safe in a flip callback, but it
means a failed send raises nothing. Check `ns.session_summary()` at the end of
a run and log the result:

```python
summary = ns.session_summary()
if not summary['ok']:
    print(summary)
```

`ok` is True only when drift correction engaged and is not stalled, NTP
sampling is current, and no event or ECI response failed. Pass
`error_log='run.jsonl'` to the constructor for the full record. See
[Diagnostics](docs/diagnostics.rst).

---

# API Summary

```python
# Connection
ns = NetStation(ip, port, debug=False, error_log=None)
ns.connect(ntp_ip=..., drift_correction=True, ...)   # both on by default
ns.begin_rec()
ns.end_rec()          # flushes queued events
ns.disconnect()       # flushes queued events, stops the sender
ns.rec_start()        # wall-clock time begin_rec() succeeded

# Events
ns.send_event(start='now', event_type='stm+', label='stm+')
ns.send_event(..., wait=True)         # block and return the ECI response
ns.flush_events(timeout=None)         # block until the queue drains
ns.pending_events()                   # how many are still queued
ns.getTime()                          # timestamp for right now
ns.time_at_monotonic(monotonic_time)  # timestamp for a captured reading

# Drift sampling
ns.sample_drift(samples=None, spacing=None)
ns.sample_drift_if_due(available_pause=None)
ns.configure_auto_drift(enabled=True, interval=15.0, min_pause=0.35,
                        background=True)
ns.set_drift_sampling(samples=4, spacing=0.05)

# Drift model
#   Only pass the options you are deliberately choosing; anything omitted
#   keeps -- and keeps tracking -- the package default.
#   ns.connect(ntp_ip=..., **{'drift_min_samples': 7})
ns.set_drift_correction(True)
ns.set_drift_requirements(min_samples=13, min_span=180.0)
ns.set_drift_model_options(max_delay=0.010, max_residual=0.003,
                           window_minutes=15.0)
ns.set_drift_stability(slew=0.0002, max_model_age=600.0, stall_after=5)
ns.refresh_drift_model()

# Inspection and diagnostics
ns.session_summary()                  # start here: one-call health check
ns.clock_state()                      # full clock and drift state
ns.drift_settings()                   # every drift setting in effect
ns.drift_estimate()
ns.drift_history()
ns.event_errors()                     # failures from asynchronous sends
ns.eci_errors()                       # failed ECI responses (not raised)
ns.set_strict_eci(True)               # make failed responses raise instead
ns.set_debug(True)                    # print ECI traffic
ns.set_error_log(path)                # JSON-lines log of everything above
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

Citation metadata is available in [CITATION.cff](CITATION.cff). A JOSS article
is in preparation; the package should be cited by its versioned archival DOI
