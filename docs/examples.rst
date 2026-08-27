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

Warming up the clock (optional)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The 25 trials take only about two minutes, which is less than the
180 seconds of evidence the drift model needs — so without help the
correction never engages during this particular run.

That is not an argument against drift correction in general, and the
numbers are worth stating precisely so it does not read as one. The
**+0.49 ms/hour** figure on the front page is a *residual* — what was
left over after a full hour of correction, not what correction removed.
Measured separately, the correction itself has removed as much as
**~3.7 ms/hour** on the NTP-visible channel, and one run showed a
further **~13 ms/hour** of marker drift from a source outside that
channel entirely (the Net Station host clock, which correction has no
way to reach regardless of whether it is enabled). Left uncorrected for
a full session, that is double-digit milliseconds an hour, accumulating
for as long as the recording runs — enough to distort or wash out later
ERP components. Keep drift correction on; it is the default and this
example does not turn it off.

What genuinely does not matter here is skipping the *warm-up*
specifically, for *this* two-minute task: even a conservative 15 ms/hour
uncorrected comes to about half a millisecond over two minutes. This
section shows the warm-up pattern for a task long enough that the
difference is worth having from trial 1, not for this one.

``warm_up_drift_model()`` collects those samples while the instructions
are on screen, using time the experiment would spend anyway, and prints
whether correction engaged before trial 1:

.. code-block:: text

    Drift correction engaged before trial 1: -22.3 ms/hour

That does mean the instruction screen sits for about 195 seconds, with a
countdown so it does not look frozen. Set ``WARMUP_SAMPLES = 0`` at the
top of the file to skip it; timestamps remain correct either way, just
uncorrected for drift until the model has enough of its own to engage on.

The warm-up has to come after ``begin_rec()``, because ``sample_drift()``
needs the NTP sync that ``begin_rec()`` performs. See
:doc:`psychopy` for why a shorter window does not work, if you are
adapting this for a task long enough that it does.

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
      --warmup 200 \
      --log photocell.csv \
      --error-log photocell_errors.jsonl

``--warmup 200`` keeps a responsive black screen up before trial 1 while
the default drift sampler gathers enough evidence to engage the model.
Omit it when starting uncorrected is useful to the validation.

Every drift setting is already at the value a
validation run wants: background sampling, the model quality gates, and
the sampling schedule are all on by default, so there is nothing to pass.
Drop ``--screen 1`` if the stimulus display is your only one.

Resist spelling the defaults out. Passing ``--drift-min-samples 13
--drift-max-residual 0.003`` and the rest changes nothing today, but it
pins those values to the version you copied them from, so a later release
that improves a default cannot reach your experiment -- and nothing
reports that it did not. Pass an option only when you are deliberately
choosing something different. To record what a run actually used, log
``ns.drift_settings()`` beside your data instead of encoding it in the
command line.

Press ``q`` or ``escape`` at any point to stop early; the CSV log is
still written and the recording closed cleanly.

Use the exported Net Station EVT file and a photocell channel to check
whether the ``stm+`` marker-to-photocell offset stays stable across the
run.

Useful options beyond the defaults::

    --drift-samples N          NTP queries per drift sample (default 4)
    --drift-sample-spacing S   seconds between queries in a burst (0.05)
    --drift-min-pause S        minimum ITI required to sample (0.35)
    --drift-cooperative        advanced: sample during ITIs instead of the
                               background thread the script uses by default
    --drift-slew R             max level-correction rate (0.0002)
    --drift-max-model-age S    stop extrapolating after this age (600)
    --drift-stall-after N      rejected fits before logging a stall (5)
    --sync-events              send with wait=True, for comparison
    --clock-timing             schedule onsets on the clock, not by frame
    --no-drift-correction      disable correction entirely

Frame-counted onsets
^^^^^^^^^^^^^^^^^^^^

Onsets are scheduled by counting frames, not by waiting on a clock, and
the run reports the display it measured:

.. code-block:: text

    Display: 60.0004 Hz, 16.6665 ms/frame (measured)
      the 3 s ISI is 0.00129 frame off a whole number, so under clock
      timing the flip phase sweeps a full frame about every 39 min
    Onset scheduling: frame

This matters more than it sounds. A refresh only 7 ppm away from an exact
divisor of the inter-stimulus interval makes the flip phase sweep slowly
through a whole frame, and the display can then present one frame later
than expected. That shows up in the photocell as a clean one-frame step
with **no software correlate at all** — identical flip gaps, identical
event timestamps, identical drift state.

It was observed directly: an hour-long run stepped +17 ms at 16 minutes
and back at 57 minutes, 40.8 minutes apart, against a predicted beat
period of 39 minutes.

Counting frames pins the flip to a constant phase and removes the effect.
``--clock-timing`` restores the old behaviour for comparison; in
simulation it reproduces the sawtooth exactly (``late_ms`` sd 4.6 ms,
range one full frame) where frame timing holds it flat.

The measured refresh, frame period, timing mode, and each trial's
``onset_frame`` are written to the CSV, and a ``display_timing`` record
goes to the error log.

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

.. note::

   **The constant offset is not a clock problem.** On the reference setup
   the marker-to-photocell offset sits at about 65 ms, and that is fixed
   hardware latency: GPU and display pipeline, photocell response, and
   amplifier filtering and sampling. Drift correction does not remove it
   and is not meant to — what it holds flat is the *variation* around it.

   The value does not transfer between machines. Re-characterize it
   whenever the display mode, refresh rate, stimulus screen position,
   amplifier sampling rate, or Net Station filter settings change.

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
