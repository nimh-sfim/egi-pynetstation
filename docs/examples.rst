Examples
========

All of these live in the repository root.

.. list-table::
   :header-rows: 1
   :widths: 42 58

   * - Script
     - What it is
   * - ``example3_stroop.py``
     - A complete Stroop task. **Start here** if you are writing an
       experiment.
   * - ``example4_psychopydemo.py``
     - The smallest possible PsychoPy loop, in the style of the bundled
       PsychoPy hardware demos.
   * - ``example.py``
     - Non-visual: connect, send timed events, disconnect. No PsychoPy
       required.
   * - ``example2.py``
     - Interactive ECI console and scripted diagnostics. See
       :doc:`diagnostics`.
   * - ``example5_psychopy_photocell_drift.py``
     - Timing validation against a photocell.
   * - ``example5_psychopy_photocell_drift_gui.py``
     - The same validation, launched from a PsychoPy dialog.

Stroop task
-----------

``example3_stroop.py`` runs five colour words in five ink colours — all
25 combinations shuffled, so 5 congruent and 20 incongruent trials.

It is written as a reference for the smallest clean setup: every line
that talks to the amplifier is marked with an ``EGI:`` comment, and there
are only seven of them.

.. code-block:: bash

    python example3_stroop.py

Edit the three addresses at the top of the file for your network.

Markers written to the recording:

.. list-table::
   :header-rows: 1
   :widths: 16 40 44

   * - Event type
     - When
     - Description field
   * - ``cong``
     - stimulus onset, word matches ink
     - —
   * - ``inco``
     - stimulus onset, word differs from ink
     - —
   * - ``resp``
     - button press
     - ``key=r incorrect target=p``
   * - ``miss``
     - no response before the timeout
     - ``no response within 2.000 s``

The response marker shows the convention worth copying — human-readable
outcome in ``desc``, machine-readable in ``data``:

.. code-block:: python

    ns.send_event(
        event_type='resp',
        label=f'key {pressed}',
        desc=f'key={pressed} {"correct" if is_correct else "incorrect"} '
             f'target={correct_key}',
        data={'trl_': index, 'key_': pressed, 'corr': is_correct,
              'rt__': rt},
    )

Marking a ``miss`` matters more than it looks. Without it, a timed-out
trial has a stimulus onset with no matching response event, which makes
epoching awkward later.

.. note::

   A 25-trial run takes roughly two minutes, so drift correction will
   probably never engage — it needs 13 NTP samples spanning 180 seconds
   by default. Timestamps are still correct, just uncorrected for drift.

Photocell timing validation
---------------------------

``example5_psychopy_photocell_drift.py`` shows a black screen with a
white dot and sends ``stm+`` on each dot onset, calling ``send_event()``
directly from the flip callback. It uses the package's own threading, so
a run validates what real experiments actually do.

.. code-block:: bash

    caffeinate -dis python example5_psychopy_photocell_drift.py amp \
      --fullscreen \
      --screen 1 \
      --duration 3600 \
      --sample-interval 15 \
      --drift-min-samples 13 \
      --drift-min-span 180 \
      --drift-max-delay 0.010 \
      --drift-max-residual 0.003 \
      --drift-window-minutes 15 \
      --log photocell.csv \
      --error-log photocell_errors.jsonl

Press ``q`` or ``escape`` at any point to stop early; the CSV log is
still written and the recording closed cleanly.

Use the exported Net Station EVT file and a photocell channel to check
whether the ``stm+`` marker-to-photocell offset stays stable across the
run.

Useful options beyond the defaults::

    --drift-samples N          NTP queries per drift sample (default 4)
    --drift-sample-spacing S   seconds between queries in a burst (0.05)
    --drift-min-pause S        minimum ITI required to sample (0.35)
    --drift-background         sample from a background thread instead
    --drift-slew R             max level-correction rate (0.0002)
    --drift-max-model-age S    stop extrapolating after this age (600)
    --drift-stall-after N      rejected fits before logging a stall (5)
    --sync-events              send with wait=True, for comparison
    --no-drift-correction      disable correction entirely

What to check in the output
^^^^^^^^^^^^^^^^^^^^^^^^^^^

#. **Steady-state offset mean and standard deviation**, excluding the
   first five minutes. The warm-up period has no correction applied and
   is systematically different.
#. **Linear trend across the run.** Should be near zero. A persistent
   slope means the correction is not tracking.
#. **``drift_rejected_fits``.** Should be flat after startup.
#. **``sys_mono_skew_ms`` versus ``drift_correction_ms``.** If the OS
   moves the system clock and the correction passes through without a
   kink, the monotonic-frame referencing is working. An OS clock
   adjustment during a run is a useful test, not a problem.

The ``--sync-events`` flag exists to quantify what the background sender
is worth. On the reference setup, ``send_call_span_ms`` in the flip
callback measures about 56 microseconds asynchronous against 7.6 ms
synchronous.

Running from the PsychoPy GUI
-----------------------------

For Windows users, or anyone who prefers PsychoPy Coder/Runner, open
``example5_psychopy_photocell_drift_gui.py`` and press Run. A startup
dialog collects the same settings and then launches the command-line
script.

For a typical amplifier run, leave the network fields blank to accept the
``amp`` defaults (``10.10.10.42``, ``10.10.10.51``, port ``55513``) and
set the stimulus screen index to match your display.
