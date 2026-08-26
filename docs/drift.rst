Drift correction
================

The stimulus computer's clock and the amplifier's clock run at slightly
different rates. Over an hour that difference is tens of milliseconds —
enough to matter, and it accumulates steadily rather than announcing
itself.

Drift correction measures that rate and compensates for it inside
:meth:`~egi_pynetstation.NetStation.NetStation.getTime`, so event
timestamps stay aligned to the amplifier without ever re-syncing the ECI
clock.

Two settings, two stages
------------------------

``drift_correction`` and ``auto_drift`` sound alike but do different jobs,
and correction only happens when both are in play. They are the two halves
of one pipeline: **collect, then apply.**

``auto_drift`` is the **producer**. It governs whether NTP drift samples
are collected, and on what schedule. Without samples there is no model.

``drift_correction`` is the **consumer**. It governs only whether
:meth:`~egi_pynetstation.NetStation.NetStation.getTime` applies the fitted
model to the timestamp it returns. With it off, ``getTime()`` returns raw
elapsed time.

.. list-table::
   :header-rows: 1
   :widths: 16 18 66

   * - ``auto_drift``
     - ``drift_correction``
     - Result
   * - on
     - on
     - **The default, and the working configuration.**
   * - on
     - off
     - Samples collected and logged, but ``getTime()`` ignores them.
       Useful for measuring drift without correcting for it.
   * - off
     - on
     - No samples are ever collected, so correction never engages. An
       explicit ``sample_drift()`` call still works.
   * - off
     - off
     - No drift machinery at all.

Both default to ``True``, and by default the package also takes care of
*who* collects the samples — see the next section.

.. _recommended-setup:

Default: background sampling
-----------------------------

**Nothing to configure.** As soon as ``drift_correction`` and
``auto_drift`` are on — which they are unless you turn them off — the
package samples NTP on its own background thread. This is the
configuration validated over repeated one-hour photocell runs, including
the cleanest run in the whole series (steady-state standard deviation
1.14 ms, zero outliers).

.. code-block:: python

    ns.connect(ntp_ip='10.10.10.51')   # background sampling starts here

No ``sample_drift_if_due()`` calls, no inter-trial-interval bookkeeping,
and no failure mode where an experiment forgets to sample: the thread
runs regardless of what the rest of your code does.

The NTP query itself runs outside every lock, so a sample cannot block
:meth:`~egi_pynetstation.NetStation.NetStation.getTime` by more than a
few microseconds — measured at 14 µs median across an hour-long run with
the thread active throughout. A background sample can in principle land
near a screen flip; in every direct comparison run against cooperative
sampling so far, this has not been observed to cost anything.

Tune the interval if you want to, same as before:

.. code-block:: python

    ns.connect(ntp_ip='10.10.10.51', auto_drift_interval=15.0)

.. _advanced-manual-sampling:

Advanced: manual sampling
--------------------------

Background sampling is the right choice for almost everyone. The two
options below exist for cases where you need explicit control over
*exactly* when an NTP query happens — for example, to guarantee one never
coincides with a specific critical window that background sampling's
own timing can't promise to avoid, or because your experiment already has
natural quiet points and you would rather use them deliberately than run
a second thread at all.

**Polling in a wait window.** Turn off background sampling and call
:meth:`~egi_pynetstation.NetStation.NetStation.sample_drift_if_due` from
a point you know is safe — an inter-trial interval, a fixation, a rest
screen:

.. code-block:: python

    ns.connect(
        ntp_ip='10.10.10.51',
        auto_drift_interval=15.0,      # a sample every 15 s
        auto_drift_min_pause=0.35,     # only in gaps at least this long
        auto_drift_background=False,   # you are taking over the sampling
    )

    # In each inter-trial interval:
    ns.sample_drift_if_due(available_pause=iti_remaining)

The package still owns the *schedule* (when a sample is due); your code
owns the *safety window* (whether now is a safe moment to take one). It
returns a status dictionary:

.. code-block:: python

    {'sampled': True,  'reason': 'due', 'sample': {...}}
    {'sampled': False, 'reason': 'not_due', 'seconds_until_due': 12.4}
    {'sampled': False, 'reason': 'pause_too_short', 'min_pause': 0.35}
    {'sampled': False, 'reason': 'disabled'}
    {'sampled': False, 'reason': 'not_synced'}

Omit ``available_pause`` if your intervals are comfortably long and you
do not want the length check.

.. warning::

   With ``auto_drift_background=False``, ``sample_drift_if_due()`` is
   the **only** thing that ever takes a sample. An experiment that
   forgets to call it collects nothing, and drift correction never
   engages, silently. This is exactly the failure mode background
   sampling exists to remove — before choosing this path, make sure
   the wait-window call really will fire on every trial.

   ``disconnect()`` warns if it detects this, and writes a
   ``drift_undersampled`` record to the error log — but that is a
   safety net, not a substitute for the call actually happening.

**Forcing a sample right now.** For the most extreme control — a single
sample at a specific, deliberately chosen instant, bypassing the schedule
and any pause check entirely — call
:meth:`~egi_pynetstation.NetStation.NetStation.sample_drift` directly:

.. code-block:: python

    sample = ns.sample_drift()

This ignores ``auto_drift`` entirely: it takes a sample unconditionally,
whether or not automatic sampling is enabled or in the middle of a
background cycle. It is what a warm-up loop uses (see
:doc:`psychopy`'s "All the commands you need to know" section) to collect
evidence during instructions, before the first trial, faster than the
schedule would on its own.

.. note::

   Drift samples are **NTP queries only**, in every one of these modes.
   None of them send an ECI clock-sync command or create a marker in the
   recording. They are safe to take as often as you like, subject only
   to blocking the calling thread for the duration of the burst.

Any of this can also be changed after connecting, or mid-session — for
example, to drop into manual control for one deliberately sensitive
stretch and hand control back to the background thread afterwards:

.. code-block:: python

    ns.configure_auto_drift(background=False)   # take over temporarily
    ...
    ns.configure_auto_drift(background=True)    # hand it back

Burst sampling
--------------

Each call makes several rapid NTP queries and keeps the **lowest-delay**
reply:

.. code-block:: python

    ns.set_drift_sampling(samples=4, spacing=0.05)   # the defaults

NTP offset error is dominated by path asymmetry, which tracks round-trip
delay, so the fastest reply in a short burst is the most trustworthy.
Selecting the minimum is considerably better than averaging, which folds
the bad replies back in. This is what ``ntpd``'s own clock filter does.

A burst blocks for about ``(samples - 1) * spacing`` plus the round
trips — roughly 170 ms at the defaults. Budget for that when choosing
``auto_drift_min_pause``.

How often to sample
-------------------

The model needs ``drift_min_samples`` valid samples spanning
``drift_min_span`` seconds before it engages — about four minutes at the
defaults, which sample every 15 seconds. Every 15 to 60 seconds is
reasonable; more frequent sampling mostly reduces noise on the slope
estimate.

How it works
------------

Three properties explain the settings.

**Offsets are referenced to the monotonic clock.** ``ntplib`` reports its
offset against the local *system* clock, but event timestamps ride the
*monotonic* clock. Those two diverge continuously, because the operating
system's time daemon (``timed`` on macOS, ``w32time`` on Windows)
disciplines the system clock. Each sample therefore records
``sys_mono_skew = local_time - monotonic_time`` and the model fits on
``offset_mono = offset + sys_mono_skew``, so OS clock adjustments cancel
algebraically instead of being injected into event timestamps.

This is not theoretical. In validation, the operating system stepped the
system clock by 256 ms mid-recording; the raw NTP offset moved by the
same amount in the opposite direction, and the monotonic-frame offset
changed by 0.15 ms. Event timing was unaffected.

**The correction is closed-loop.** When a new fit is accepted, the model
anchors on the current corrected offset — so timestamps never step — but
it also records the difference between that anchor and the level the new
fit actually measures, and retires that difference at a bounded rate.
Without this the correction would be an open-loop integral of noisy
slope estimates and would random-walk away from truth over a long
recording.

**Stale models stop extrapolating.** If fits are being rejected, the last
accepted slope is extrapolated only up to ``drift_max_model_age``, after
which the correction holds its value rather than running away.

Tuning
------

Correction is gated so early NTP noise cannot produce a bad
extrapolation:

.. code-block:: python

    ns.set_drift_requirements(min_samples=13, min_span=180.0)

Quality thresholds and the fitting window:

.. code-block:: python

    ns.set_drift_model_options(
        max_delay=0.010,      # reject NTP samples with >10 ms round trip
        max_residual=0.003,   # reject fits outside +/-3 ms residual
        window_minutes=15.0,  # fit on the last 15 minutes of valid samples
    )

For a 250 Hz recording one sample is 4 ms, so ``max_residual=0.004``
relaxes the fit-quality gate to a one-sample budget.

.. tip::

   **Prefer the rolling window over a cumulative fit.** Setting
   ``window_minutes=0.0`` fits all valid samples, which sounds more
   stable but tracks a curving offset series more poorly — an old sample
   is evidence about an old clock rate. The 15-minute default is the
   validated configuration.

Stability controls:

.. code-block:: python

    ns.set_drift_stability(
        slew=0.0002,          # max level correction per second elapsed
        max_model_age=600.0,  # stop extrapolating a fit after this age
        stall_after=5,        # rejected fits before logging a stall
    )

``slew`` bounds how fast an outstanding level error is retired, so
accepting a new fit never steps event timestamps. Use ``0`` to apply
level corrections instantly.

Forcing a refit from the samples already collected, without querying NTP
or sending any ECI command:

.. code-block:: python

    estimate = ns.refresh_drift_model()

Settings reference
------------------

.. list-table::
   :header-rows: 1
   :widths: 30 14 56

   * - Setting
     - Default
     - Meaning
   * - ``drift_correction``
     - ``True``
     - Applies the fitted model in ``getTime()``. Disabling it stops
       correction but not sampling.
   * - ``drift_min_samples``
     - ``13``
     - Valid NTP samples required before correction applies.
   * - ``drift_min_span``
     - ``180.0`` s
     - Elapsed time the samples must cover.
   * - ``drift_max_delay``
     - ``0.010`` s
     - Reject samples with a higher round-trip delay.
   * - ``drift_max_residual``
     - ``0.003`` s
     - Reject fits whose maximum absolute residual exceeds this.
   * - ``drift_window_minutes``
     - ``15.0`` min
     - Fit on this many recent minutes. ``0`` uses all valid samples.
   * - ``drift_samples``
     - ``4``
     - NTP queries per sample; the lowest-delay reply is kept.
   * - ``drift_sample_spacing``
     - ``0.05`` s
     - Seconds between queries inside one burst.
   * - ``drift_slew``
     - ``0.0002``
     - Max seconds of level correction retired per second elapsed.
   * - ``drift_max_model_age``
     - ``600.0`` s
     - Stop extrapolating a fit past this age. ``0`` is unbounded.
   * - ``auto_drift``
     - ``True``
     - Enable the drift sampling schedule. Pass ``False`` to disable.
   * - ``auto_drift_interval``
     - ``15.0`` s
     - Target seconds between drift samples.
   * - ``auto_drift_min_pause``
     - ``0.35`` s
     - Minimum idle time before a cooperative sample is taken.
   * - ``auto_drift_background``
     - ``True``
     - Sample from a package-owned thread. Set ``False`` for manual,
       advanced control instead. See :ref:`advanced-manual-sampling`.

Setting options programmatically
--------------------------------

Every setting in the table is a keyword argument of ``connect()``, so a
dict of the ones you are choosing deliberately can be splatted in:

.. code-block:: python

    drift_opts = {
        'drift_min_samples': 7,
        'drift_window_minutes': 9.0,
    }
    ns.connect(ntp_ip='10.10.10.51', **drift_opts)

Include only what you are actually choosing. Everything you leave out
keeps the package default, and — this is the point — keeps tracking it,
so a later release that improves a default reaches your experiment.

To read the settings currently in effect, for a log or to populate a
launcher dialog:

.. code-block:: python

    ns.drift_settings()

.. code-block:: python

    {'drift_correction': True, 'drift_min_samples': 13,
     'drift_min_span': 180.0, 'drift_max_delay': 0.01,
     'drift_max_residual': 0.003, 'drift_window_minutes': 15.0,
     'drift_samples': 4, 'drift_sample_spacing': 0.05,
     'drift_slew': 0.0002, 'drift_max_model_age': 600.0,
     'drift_stall_after': 5, 'auto_drift': True,
     'auto_drift_interval': 15.0, 'auto_drift_min_pause': 0.35,
     'auto_drift_background': True}

It works before ``connect()`` as well, which is the only straightforward
way to obtain the real defaults: every drift parameter of ``connect()``
has a signature default of ``None``, meaning *leave unchanged*, so
inspecting the signature reports nothing useful.

``drift_window_minutes`` and ``drift_max_model_age`` report ``0`` for "no
limit", matching what ``connect()`` accepts.

.. warning::

   ``drift_settings()`` is a report, not a configuration template. Saving
   the whole dictionary into an experiment scaffold pins all of these
   values at the version you captured them: a later release that improves
   a default can no longer reach that experiment, and nothing warns you.
   Keep a dict of your deliberate choices instead.

   ``drift_stall_after`` is reported for completeness but is not a
   ``connect()`` argument — it is set afterwards with
   :meth:`~egi_pynetstation.NetStation.NetStation.set_drift_stability` —
   so passing the whole dictionary to ``connect()`` raises ``TypeError``
   rather than silently misconfiguring the session.

Checking that it worked
-----------------------

See :doc:`diagnostics`. The short version: after a run,
``drift_accepted_fits`` should be non-zero and ``drift_rejected_fits``
should be flat after startup.
