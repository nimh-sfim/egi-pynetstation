Advanced guide
==============

This page explains the machinery behind the five-command workflow. Nothing
here is required for a normal experiment.

.. _advanced-connections:

Connections and recording epochs
--------------------------------

The constructor holds the Net Station command address and ECI port. The NTP
address is passed to ``connect()``:

.. code-block:: python

   ns = NetStation(
       '10.10.10.42',
       55513,
       error_log='run.jsonl',
   )
   ns.connect(ntp_ip='10.10.10.51', drift_warmup=True)

``begin_rec()`` sends ``BeginRecording`` and performs the ECI NTP clock sync.
That sync establishes time zero for the recording. Do not call ``ntpsync()``
again inside the recording: it would establish a different epoch.

One connection may contain multiple recordings. Each new ``begin_rec()``
performs a new sync. Existing drift samples are translated into the new
elapsed-time coordinate, and the active slope is re-anchored to the new sync
observation. The long-baseline evidence is retained without producing a
timestamp step.

Recording control is strict: a rejected begin, end, or clock sync raises.
Ordinary event responses are tolerant by default so one bad marker does not
end a recording already in progress.

Byte order
~~~~~~~~~~

The default ``endian='NTEL'`` is correct for current Intel, AMD, and ARM64
computers, including Intel and Apple silicon Macs. ``NTEL`` is the ECI token
for little-endian data, not a processor brand. ``MAC-`` and ``UNIX`` are
legacy big-endian tokens.

.. _advanced-events:

Events and timestamps
---------------------

``send_event(start='now')`` captures the package's high-resolution monotonic
clock on the caller's thread. It places the event on a queue and returns;
another thread applies drift correction and performs ECI network I/O.

The event timestamp therefore describes when ``send_event()`` was called, not
when the socket write completed.

Explicit capture and conversion
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Capture a time on a critical path and convert it later:

.. code-block:: python

   captured = ns.capture_time()
   # later
   start = ns.time_at_capture(captured)
   ns.send_event(start=start, event_type='stim')

Use this pattern when an integration cannot call ``send_event()`` directly at
the moment being marked.

Asynchronous errors
~~~~~~~~~~~~~~~~~~~

A non-blocking call cannot raise an error that happens later on its worker
thread. Inspect:

.. code-block:: python

   ns.pending_events()
   ns.flush_events()
   ns.event_errors()
   ns.eci_errors()
   ns.session_summary()

``end_rec()`` and ``disconnect()`` flush queued events automatically. Use
``send_event(wait=True)`` only when a diagnostic needs the parsed response;
never use it in a PsychoPy flip callback.

Pass ``strict_eci=True`` to ``connect()`` when a diagnostic run should stop on
the first rejected or malformed event response.

.. _advanced-drift:

Drift correction
----------------

The stimulus computer and amplifier use different oscillators. Their clock
rates differ, so a single sync accumulates error over time. The package samples
the amplifier's NTP server, fits the offset rate, and corrects timestamps
without repeatedly changing the ECI epoch.

Sampling and correction are both enabled by default. A background thread takes
one best-of-four NTP sample every 15 seconds. No trial-loop hook is required.

The two-stage model
~~~~~~~~~~~~~~~~~~~

The stable model requires 13 valid samples spanning at least 180 seconds. On a
Windows rig with large startup drift, waiting three minutes uncorrected is
visible at the photocell.

Enable the provisional model:

.. code-block:: python

   ns.connect(ntp_ip=amp_ip, drift_warmup=True)

It uses at least five samples spanning 20 seconds and samples every five
seconds until ready. The stable model promotes as soon as its 180-second gates
are met and never falls back to the provisional model. Both activations are
continuous: a new fit is anchored to the correction already being applied,
and any small level difference is retired at the configured slew rate.

Model records use ``drift_model_engaged`` for the provisional activation and
``drift_model_promoted`` for the stable takeover. ``clock_state()`` reports
``active_drift_model_stage`` and ``drift_stable_engaged``.

Starting before the recording
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Background sampling starts at ``connect()``. Samples collected before
``begin_rec()`` are retained by default and receive their elapsed coordinates
when the sync establishes the epoch. Connecting during participant setup can
therefore put the stable model in place before trial one.

Set ``drift_presync=False`` only when pre-recording NTP traffic is unwanted.

Readiness
~~~~~~~~~

Correction activates automatically. These methods are only for experiments
that want to wait for it or log its state:

.. code-block:: python

   status = ns.drift_ready()
   status = ns.wait_for_drift(timeout=240)

``drift_ready()`` can report ``warming_up``, ``settling``, ``stalled``,
``model_expired``, or ``sampling_expired``. ``wait_for_drift()`` returns the
final status rather than raising on timeout.

Do not call either from a flip callback.

Manual sampling
~~~~~~~~~~~~~~~

Background sampling is recommended. For a paradigm that must guarantee no NTP
traffic near a critical stimulus:

.. code-block:: python

   ns.connect(
       ntp_ip=amp_ip,
       auto_drift_background=False,
   )

   # Call only during a known quiet interval.
   ns.sample_drift_if_due(available_pause=0.5)

``sample_drift_if_due()`` follows the configured schedule and checks the pause
budget. ``sample_drift()`` ignores the schedule and samples immediately. A
default four-query burst takes roughly 150--200 ms, so neither belongs on a
critical path.

Settings
~~~~~~~~

The defaults are conservative. Change only the settings a validation result
gives you a reason to change.

.. list-table::
   :header-rows: 1
   :widths: 31 18 51

   * - ``connect()`` option
     - Default
     - Purpose
   * - ``drift_warmup``
     - ``False``
     - Enable the 20-second provisional model.
   * - ``drift_min_samples`` / ``drift_min_span``
     - ``13`` / ``180 s``
     - Stable-model evidence gates.
   * - ``drift_warmup_min_samples`` / ``drift_warmup_min_span``
     - ``5`` / ``20 s``
     - Provisional-model evidence gates.
   * - ``drift_warmup_interval``
     - ``5 s``
     - Sampling interval before stable promotion.
   * - ``auto_drift_interval``
     - ``15 s``
     - Sampling interval after stable promotion.
   * - ``drift_samples`` / ``drift_sample_spacing``
     - ``4`` / ``0.05 s``
     - Queries per burst and spacing; the lowest-delay reply wins.
   * - ``drift_max_delay``
     - ``0.010 s``
     - Reject slower NTP replies from fitting.
   * - ``drift_max_residual``
     - ``0.003 s``
     - Reject a fitted line that does not describe its samples.
   * - ``drift_window_minutes``
     - ``15 min``
     - Rolling history used by the fit; ``0`` means all samples.
   * - ``drift_slew``
     - ``0.0002 s/s``
     - Maximum rate for retiring a fit's level difference.
   * - ``drift_max_model_age``
     - ``600 s``
     - Stop extrapolating a stale slope; ``0`` is unlimited.

The corresponding runtime methods are ``set_drift_requirements()``,
``set_drift_warmup()``, ``set_drift_model_options()``,
``set_drift_sampling()``, ``configure_auto_drift()``, and
``set_drift_stability()``. ``drift_settings()`` reports the current values.

Monitoring
~~~~~~~~~~

Accepted fits can still be fed a bad sample or experience a level excursion.
The default monitor:

* logs a level excursion above 5 ms,
* rejects an individual offset more than 100 ms from the active model, and
* writes a periodic model-status record every 120 seconds.

Tune these with ``set_drift_monitoring()``. Set an individual value to ``0``
to disable that monitor.

.. _advanced-psychopy:

PsychoPy and display timing
---------------------------

Use ``win.callOnFlip(ns.send_event, ...)`` for visual onsets. The callback
should capture state, not query the network or filesystem.

Frame scheduling and event timing are different layers. A marker may be timed
correctly while the display presents a frame late. Prefer whole-frame
durations and inspect PsychoPy frame intervals in photocell validation.

The photocell example's ``--frame-interval-log`` output contains:

* frame index and cumulative elapsed time,
* the measured interval in seconds and milliseconds,
* a long-frame flag at 1.5 nominal refresh periods, and
* an estimated number of missed refreshes.

A multi-second photocell mismatch with a sub-millisecond event-send call and a
large frame interval points to the display or photocell path rather than ECI
clock drift.

.. _advanced-platforms:

Platform setup
--------------

Windows
~~~~~~~

The package uses ``QueryPerformanceCounter`` for capture time and a precise
Windows wall clock for NTP I/O. Run:

.. code-block:: bash

   python -m egi_pynetstation.check_clocks --compare

This also shows the standard Python clocks and sleep behavior used by other
libraries such as PsychoPy. Use Python 3.13 or newer when practical. Keep the
stimulus window visible and in the foreground during validation.

macOS
~~~~~

Prevent display and idle sleep during a run:

.. code-block:: bash

   caffeinate -dis python my_experiment.py

Also disable the screen saver and use AC power for long validation sessions.

.. _advanced-diagnostics:

Diagnostics
-----------

Start with one call:

.. code-block:: python

   summary = ns.session_summary()

``ok`` requires an engaged, non-stalled drift model, current sampling, and no
event or ECI response failures. A very short experiment can finish before any
model engages; in that case ``ok`` is false even though its markers may be
usable.

For detail:

``clock_state()``
   Full sync, fit, sampling, and active-correction state.

``drift_estimate()``
   Current fitted and active slope, residuals, sample counts, and stage.

``drift_history()``
   Every retained NTP offset observation.

``event_errors()`` / ``eci_errors()``
   Asynchronous event failures and rejected or malformed ECI responses.

``clock_report()``
   Selected clocks and their measured resolution.

Pass ``error_log='run.jsonl'`` to the constructor for a persistent JSON-lines
record. Important ``record`` values include:

* ``session_start``
* ``drift_model_engaged`` and ``drift_model_promoted``
* ``drift_model_stalled`` and ``drift_model_recovered``
* ``drift_sampling_failed`` and ``drift_sampling_recovered``
* ``drift_level_excursion`` and ``drift_level_recovered``
* ``drift_sample_rejected`` and ``drift_model_status``
* ``event_send_error`` and ``eci_response_error``

.. _advanced-validation:

Photocell validation
--------------------

Use the repository's Example 5 on each stimulus computer before production:

.. code-block:: powershell

   python example5_psychopy_photocell_drift.py amp `
       --sessions 2 `
       --duration 600 `
       --staged-drift `
       --fullscreen `
       --log C:\logs\timing.csv `
       --error-log C:\logs\timing_errors.jsonl `
       --frame-interval-log C:\logs\frames.csv

Check:

1. photocell offset range, standard deviation, and trend;
2. continuity at provisional engagement, stable promotion, and recording
   boundaries;
3. NTP sample validity and fit residuals;
4. event-send failures and queue depth; and
5. long frames at any photocell outlier.

Keep the raw photocell export, event CSV, JSONL error log, and frame CSV
together. They describe different layers of the same timing path.

Server-clock diagnostics
------------------------

``sync_return_clock()`` and forced ``ntpsync()`` are diagnostic operations,
not drift maintenance. They can add markers or change the current timestamp
epoch. Production experiments should use the background drift model instead.
