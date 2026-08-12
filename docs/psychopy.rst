Using PsychoPy
==============

Send markers straight from ``win.callOnFlip()``. The package owns the
threading, the timestamp capture, and the flushing, so there is nothing
to configure.

.. code-block:: python

    from psychopy import visual, core
    from egi_pynetstation import NetStation

    ns = NetStation('10.10.10.42', 55513)
    ns.connect(
        ntp_ip='10.10.10.51',
        auto_drift_interval=15.0,
        auto_drift_min_pause=0.35,
    )

    win = visual.Window(fullscr=True, screen=1, color='black')
    stim = visual.TextStim(win, text='+')

    try:
        ns.begin_rec()

        for trial in range(100):
            stim.draw()
            win.callOnFlip(ns.send_event, event_type='stm+', label='stimulus')
            win.flip()
            core.wait(0.5)

            # Inter-trial interval: offer the package the idle time it may
            # use. It samples only if one is due and there is room for it.
            win.flip()
            ns.sample_drift_if_due(available_pause=1.0)
            core.wait(1.0)
    finally:
        ns.end_rec()      # flushes any queued events
        ns.disconnect()

That is the whole integration — no ``queue``, no ``threading``, no manual
timestamp bookkeeping. A complete working version is
:doc:`example3_stroop.py <examples>`.

Why this is safe in a flip callback
-----------------------------------

``send_event()`` captures ``time.monotonic()`` on the calling thread —
the one aligned with your stimulus — puts the event on a queue, and
returns. A background worker converts that reading into a
drift-corrected timestamp and writes to the socket.

Measured block time on the calling thread is about **56 microseconds**.
The same send performed inline, waiting for the amplifier's reply, takes
about **7.6 ms** and pushes the following frame.

Critically, the timestamp describes **when the flip happened**, not when
the network write completed. A slow or backlogged send cannot move your
event timing.

Two consequences:

* ``send_event()`` returns ``None``, because there is no response yet.
* Send failures cannot raise into your experiment code, so they are
  collected instead. Check them at the end of a run:

.. code-block:: python

    errors = ns.event_errors()
    if errors:
        print(f'{len(errors)} events failed to send:', errors[:3])

Queued events are flushed automatically by ``end_rec()``,
``disconnect()``, and again at interpreter exit as a safety net, so a
script that crashes or forgets to disconnect still delivers what it
queued. Call
:meth:`~egi_pynetstation.NetStation.NetStation.flush_events` yourself
only if you need a synchronisation point mid-experiment.

Mixing flip markers and ordinary markers
----------------------------------------

There is one setting, and you rarely need it. ``send_event()`` never
blocks; ``send_event(wait=True)`` blocks and returns the parsed ECI
response.

That means both usages are the same call and can be mixed freely:

.. code-block:: python

    # Visual onset: timestamped at the flip.
    win.callOnFlip(ns.send_event, event_type='stm+')
    win.flip()

    # Response marker: timestamped at this line.
    ns.send_event(event_type='resp', desc=f'key={key}')

Neither blocks, both capture their timestamp on the calling thread, and
order is preserved. The only difference is *which moment* each one marks
— the flip, or the line of code.

Use ``callOnFlip`` when the marker must line up with what the participant
saw. Call it directly for anything else: responses, trial boundaries,
block starts.

.. note::

   Both paths have the same timing precision. ``callOnFlip`` is not more
   accurate — it just marks a different instant. Choose based on what you
   want to mark, not on which is faster.

Pass ``wait=True`` when you actually need the amplifier's reply, such as
in a diagnostic console. Do **not** use it inside a flip callback.

What not to do in a flip callback
---------------------------------

Do not call
:meth:`~egi_pynetstation.NetStation.NetStation.clock_state` or
:meth:`~egi_pynetstation.NetStation.NetStation.drift_estimate` there.
Both build a full diagnostic snapshot. Call them between trials.

Do not call
:meth:`~egi_pynetstation.NetStation.NetStation.sample_drift` there
either. It performs several NTP queries and blocks for roughly 170 ms at
default settings. See :doc:`drift`.

Timestamping without the callback
---------------------------------

If your framework does not offer a flip callback, capture a raw clock
reading at the critical moment and convert it later:

.. code-block:: python

    import time

    captured = time.monotonic()        # cheap: no locks, no model work
    # ... after the frame has appeared ...
    ns.send_event(start=ns.time_at_monotonic(captured), event_type='stm+')

:meth:`~egi_pynetstation.NetStation.NetStation.time_at_monotonic` returns
the event timestamp for the instant of capture, not the instant of
conversion.

Stimulus computer setup
-----------------------

* Run under ``caffeinate -dis`` on macOS and disable the screen saver.
  See :ref:`check-your-clocks` and the notes in :doc:`installation`.
* Run from AC power.
* On Windows, add Defender exclusions for the experiment directory and
  the Python environment; real-time scanning is a common source of
  multi-millisecond hiccups.

Warm-up
-------

Drift correction does not engage until the model has enough evidence —
13 samples spanning 180 seconds by default, so roughly four minutes at a
15-second interval. Measured bias during that window is about
**-0.94 ms** relative to steady state.

If you intend to run without a photocell and rely on a previously
characterised constant offset, collect drift samples during setup or
instructions so the model is live before the first trial.
