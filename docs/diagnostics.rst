Diagnostics
===========

Runtime state
-------------

:meth:`~egi_pynetstation.NetStation.NetStation.clock_state` returns a flat
dictionary that is convenient to log once per trial:

.. code-block:: python

    state = ns.clock_state()
    print(state['drift_slope'] * 1000 * 3600, 'ms/hour')

Related accessors:

* :meth:`~egi_pynetstation.NetStation.NetStation.drift_history` — every
  sample collected, including ones rejected for delay.
* :meth:`~egi_pynetstation.NetStation.NetStation.drift_estimate` — the
  current fit and prediction.
* :meth:`~egi_pynetstation.NetStation.NetStation.event_errors` — failures
  from asynchronous sends.
* :meth:`~egi_pynetstation.NetStation.NetStation.eci_errors` — ECI
  responses that failed or did not parse, recorded rather than raised.
* :meth:`~egi_pynetstation.NetStation.NetStation.session_summary` — one
  call covering drift, event, ECI, and NTP-sampling health.
* :meth:`~egi_pynetstation.NetStation.NetStation.pending_events` — how
  many events are still queued.

What healthy looks like
-----------------------

.. list-table::
   :header-rows: 1
   :widths: 30 22 48

   * - Field
     - Healthy
     - Meaning
   * - ``drift_accepted_fits``
     - non-zero
     - Zero means correction never engaged at all.
   * - ``drift_rejected_fits``
     - flat after startup
     - Climbing means a discontinuity, or noise beyond
       ``drift_max_residual``.
   * - ``drift_stalled``
     - ``False``
     - ``True`` means fits are being refused right now.
   * - ``drift_pending_error``
     - near zero
     - Level error the closed loop is still retiring.
   * - ``drift_model_age``
     - near zero
     - Seconds since the active fit was anchored.
   * - ``active_drift_slope``
     - stable
     - Jumping between values suggests a clock discontinuity.
   * - ``sys_mono_skew``
     - may move freely
     - OS clock discipline. Movement here is expected and should
       **not** affect timestamps.
   * - ``drift_last_reject_reason``
     - ``None``
     - Otherwise ``too_few_samples``, ``short_span``,
       ``degenerate_span``, or ``high_residual``.

Startup rejections are normal. ``too_few_samples`` and ``short_span``
simply mean the model has not seen enough evidence yet, which is the
expected state for the first few minutes.

The error log
-------------

Pass ``error_log`` when constructing the station to get a JSON-lines
record of anything that goes wrong:

.. code-block:: python

    ns = NetStation('10.10.10.42', 55513, error_log='session_errors.jsonl')

It can also be set later with
:meth:`~egi_pynetstation.NetStation.NetStation.set_error_log`. Parent
directories are created for you. If the path turns out to be unwritable,
the failure is reported through Python's ``logging`` and the recording
continues — losing the log is bad, losing the recording is worse.

Each line carries a ``record`` field:

.. list-table::
   :header-rows: 1
   :widths: 28 72

   * - ``record``
     - Written when
   * - ``session_start``
     - ``connect()`` succeeds. Captures the drift settings in force, so a
       log file says what produced it.
   * - ``eci_response_error``
     - The amplifier returned something unparseable. Includes ``cmd``,
       the raw bytes as ``raw_hex`` and a printable ``raw_display``, and
       — for events — ``event_type``, ``label``, and ``start``.
   * - ``event_send_failure``
     - An asynchronous send raised. Includes ``event_type``, ``label``,
       and ``start``.
   * - ``drift_model_engaged``
     - Drift correction became active for the first time. Records the
       fitted slope, sample count, and span.
   * - ``drift_model_stalled``
     - Fits have been rejected ``stall_after`` times in a row after the
       model had engaged.
   * - ``drift_model_recovered``
     - A fit was accepted again. Records ``stall_duration`` and
       ``rejected_during_stall``.
   * - ``drift_undersampled``
     - Auto-drift was enabled but almost nothing was sampled — usually a
       missing ``sample_drift_if_due()`` call.
   * - ``drift_sampling_failed``
     - NTP queries have failed several bursts in a row. Written once on
       entry to an outage, not once per interval.
   * - ``drift_sampling_recovered``
     - Sampling started working again. Records
       ``failures_during_outage``.

Every failure record also carries a ``clock`` snapshot: the full
``clock_state()`` at the moment of the error. That is usually what
explains it.

When sampling stops entirely
----------------------------

A stalled drift model and a *sampling outage* are different failures, and
only the first is visible to ``drift_stalled``. The stall detector counts
**rejected fits**. If every NTP query fails, no sample is recorded, so no
fit is attempted, so nothing is rejected — ``drift_stalled`` stays
``False`` while the applied correction silently freezes at its last
value.

``session_summary()`` covers this separately:

.. code-block:: python

    summary = ns.session_summary()
    if summary['ntp_sampling_stale']:
        print(summary['ntp_sample_failures'], 'failed bursts;',
              summary['ntp_seconds_since_success'], 's since a good one')

``ntp_sampling_stale`` becomes True when background sampling is expected
but the last success is older than ``max(2 x interval,
drift_max_model_age)``, and it forces ``ok`` to False. The same fields,
plus ``ntp_consecutive_failures`` and ``ntp_last_error``, appear in
``clock_state()``.

Cooperative sampling is never judged stale: with
``auto_drift_background=False`` the experiment owns the schedule, so the
package cannot tell a missed sample from a deliberate one.

Why the drift records matter
----------------------------

A stalled drift model is silent by construction. Fits are refused, the
last accepted slope keeps being extrapolated, and nothing raises until
the timing error has already grown. In one hour-long validation run this
went unnoticed for 15 minutes and cost about 17 ms.

Now it is one line, while it is happening:

.. code-block:: json

    {"record": "drift_model_stalled", "elapsed": 330.0,
     "drift_consecutive_rejections": 3,
     "drift_last_reject_reason": "high_residual",
     "drift_model_age": 45.0, "extrapolation_frozen": false,
     "active_slope_ms_per_hour": 36.0}

Reading a log:

.. code-block:: python

    import json

    for line in open('session_errors.jsonl'):
        record = json.loads(line)
        if record['record'] == 'event_send_failure':
            print(record['event_type'], record['error'],
                  'model age:', record['clock']['drift_model_age'],
                  'rejected fits:', record['clock']['drift_rejected_fits'])

Failed ECI responses
--------------------

A response the amplifier rejects, or one that does not parse at all, is
**recorded rather than raised**. ``send_event(wait=True)`` returns a
diagnostic dictionary with ``ok: False`` instead of throwing, so a single
bad reply cannot end a recording in progress.

Every one of them is kept where you can find it afterwards:

.. code-block:: python

    for failure in ns.eci_errors():
        print(failure['cmd'], failure['error'], failure['raw_display'])

``eci_errors()`` holds the most recent 100 failures with the command that
caused them and, for events, the ``event_type`` and ``label`` — so a bad
marker can be traced to its trial without opening the log file. The count
also appears in :meth:`~egi_pynetstation.NetStation.NetStation.session_summary`
as ``eci_response_failures``, and any non-zero value makes ``ok`` False.
The error log, if configured, keeps the complete history.

If you would rather stop at the first sign of trouble — a diagnostic
session rather than a live recording — opt into raising:

.. code-block:: python

    ns.connect(ntp_ip='10.10.10.51', strict_eci=True)
    # or, at any point:
    ns.set_strict_eci(True)

That applies uniformly to unparseable responses and to failures the
amplifier reports.

Interactive ECI console
-----------------------

``example2.py`` is an interactive command sender and scripted diagnostic
runner. It prints sent bytes, raw responses, parsed responses, drift
samples, and clock state.

.. code-block:: bash

    python example2.py amp \
      --experiment experiments/experiment_24.txt \
      --transcript exp24.txt \
      --error-log error24.jsonl \
      --no-interactive

Useful experiment-file commands::

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

It sends its events with ``wait=True``, because it prints the parsed ECI
response for every command and therefore needs the reply. That is the
right call for a console; experiments should keep the non-blocking
default.

Timing validation
-----------------

``example5_psychopy_photocell_drift.py`` is the validation harness. See
:doc:`examples`.
