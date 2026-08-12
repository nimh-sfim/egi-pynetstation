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

Both default to ``True``, so the working configuration is what you get
without asking. What remains yours to arrange is *who takes the samples* —
see the next two sections.

.. _recommended-setup:

Recommended setup
-----------------

**Keep the defaults and sample during inter-trial intervals.** Drift
correction and the sampling schedule are both on already, so all that is
left is tuning the interval and calling
:meth:`~egi_pynetstation.NetStation.NetStation.sample_drift_if_due`. That
is the configuration validated over repeated one-hour photocell runs.

.. code-block:: python

    ns.connect(
        ntp_ip='10.10.10.51',
        auto_drift_interval=15.0,     # a sample every 15 s
        auto_drift_min_pause=0.35,    # only in gaps at least this long
    )

then, in each inter-trial interval:

.. code-block:: python

    ns.sample_drift_if_due(available_pause=iti_remaining)

This is the arrangement used by :doc:`example3_stroop.py <examples>` and
by the photocell validator. It keeps NTP queries away from screen flips
by construction, because your experiment is the thing that knows when a
safe gap exists.

.. warning::

   ``auto_drift`` sets a **schedule**, not a worker.
   ``sample_drift_if_due()`` is the only thing that acts on it. Because
   the schedule is on by default, it is easy to assume sampling is
   handled: an experiment that never calls it collects no samples at all,
   drift correction never engages, and nothing else complains.

   ``disconnect()`` will warn if it detects this, and writes a
   ``drift_undersampled`` record to the error log — but that is a safety
   net, not a substitute for arranging the sampling.

Background sampling
-------------------

If your experiment has no convenient inter-trial intervals, or you would
simply rather not think about it, the package can sample on its own
thread:

.. code-block:: python

    ns.connect(
        ntp_ip='10.10.10.51',
        auto_drift_interval=15.0,
        auto_drift_background=True,
    )

Nothing else is required — no ``sample_drift_if_due()`` calls, and the
undersampling failure mode disappears entirely.

The tradeoff is small but real. The NTP query itself runs outside every
lock, so a background sample cannot block ``getTime()`` by more than a
few microseconds. What it *can* do is put a network wakeup and a thread
switch near a screen flip, which the cooperative arrangement avoids by
design.

.. list-table::
   :header-rows: 1
   :widths: 22 39 39

   * -
     - Cooperative *(recommended)*
     - Background
   * - Setup
     - nothing; it is the default
     - ``auto_drift_background=True``
   * - Your experiment must
     - call ``sample_drift_if_due()`` in quiet periods
     - nothing
   * - Sampling near a flip
     - never, by construction
     - possible
   * - Forgetting to wire it up
     - no samples collected
     - cannot happen
   * - Best for
     - visual experiments with usable ITIs
     - long runs, non-visual work, anything without clean gaps

Both can also be set after connecting, or changed mid-session:

.. code-block:: python

    ns.configure_auto_drift(enabled=True, interval=15.0, background=False)

Sampling by hand
----------------

To manage the schedule yourself, call
:meth:`~egi_pynetstation.NetStation.NetStation.sample_drift` from a point
you know is safe. It returns the recorded sample.

``sample_drift_if_due()`` returns a status dictionary instead:

.. code-block:: python

    {'sampled': True,  'reason': 'due', 'sample': {...}}
    {'sampled': False, 'reason': 'not_due', 'seconds_until_due': 12.4}
    {'sampled': False, 'reason': 'pause_too_short', 'min_pause': 0.35}
    {'sampled': False, 'reason': 'disabled'}
    {'sampled': False, 'reason': 'not_synced'}

Omit ``available_pause`` if your intervals are comfortably long and you
do not want the length check.

.. note::

   Drift samples are **NTP queries only**. They send no ECI clock-sync
   command and create no markers in the recording. They are safe to take
   as often as you like, subject only to blocking the calling thread.

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
     - ``False``
     - Sample from a package-owned thread.

Checking that it worked
-----------------------

See :doc:`diagnostics`. The short version: after a run,
``drift_accepted_fits`` should be non-zero and ``drift_rejected_fits``
should be flat after startup.
