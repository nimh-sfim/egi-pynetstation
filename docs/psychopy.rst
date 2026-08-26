Using PsychoPy
==============

Quickstart: background sampling is already on
----------------------------------------------

The default setup is the recommended setup for PsychoPy.  Drift correction
and the background thread that supplies it with NTP samples are both enabled
automatically.  Send markers straight from ``win.callOnFlip()``; there is no
sampling call to add to the trial loop and no drift option to configure.

.. code-block:: python

    from psychopy import visual, core
    from egi_pynetstation import NetStation

    ns = NetStation('10.10.10.42', 55513)
    ns.connect(ntp_ip='10.10.10.51')   # drift sampling starts on its own thread

    win = visual.Window(fullscr=True, screen=1, color='black')
    stim = visual.TextStim(win, text='+')

    try:
        ns.begin_rec()

        for trial in range(100):
            stim.draw()
            win.callOnFlip(ns.send_event, event_type='stm+', label='stimulus')
            win.flip()
            core.wait(0.5)
            core.wait(1.0)
    finally:
        ns.end_rec()      # flushes any queued events
        ns.disconnect()

.. important::

   The example above is complete.  As long as you leave
   ``auto_drift_background`` at its default of ``True``, do **not** add
   ``sample_drift_if_due()`` or ``sample_drift()`` calls.  The package takes
   samples on its own thread.

   The :ref:`manual-drift-sampling` section is only for experiments that
   deliberately pass ``auto_drift_background=False``.  Default users can
   skip it entirely.

There is no ``queue``, no ``threading``, no manual timestamp bookkeeping,
and no inter-trial sampling hook to maintain. A complete working version is
:doc:`example3_stroop.py <examples>`.

Core calls in the default setup
-------------------------------

Six calls cover a complete experiment. Everything else in this package is
diagnostics or optional tuning.

``ns.connect(...)``
^^^^^^^^^^^^^^^^^^^

Opens the ECI connection and configures the clock machinery. Drift
correction is on already, and so is sampling for it — a background thread
takes NTP samples on its own, so most experiments only need the amplifier
address:

.. code-block:: python

    ns.connect(ntp_ip='10.10.10.51')

Pass ``auto_drift_background=False`` if you need explicit control over
exactly when sampling happens instead. If you do not pass that option, skip
:ref:`manual-drift-sampling`. See :doc:`drift` for the full set of advanced
manual-sampling options.

``ns.begin_rec()``
^^^^^^^^^^^^^^^^^^

Starts the recording, and performs the one ECI ``NTPClockSync`` that
establishes the event timestamp epoch. Every event timestamp is measured
from this moment, so it must come before any events.

.. code-block:: python

    ns.begin_rec()

Call it exactly once per recording. Re-syncing mid-recording resets the
epoch and puts a discontinuity in your timestamps.

``win.callOnFlip(ns.send_event, ...)``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

PsychoPy's own mechanism: run this function on the next flip. Because
``send_event()`` is non-blocking, it is safe to hand it directly to
``callOnFlip`` — the marker is then timestamped at the flip that made the
stimulus visible.

.. code-block:: python

    stim.draw()
    win.callOnFlip(ns.send_event, event_type='stm+', label='stimulus')
    win.flip()

Draw first, register the callback, then flip. Any keyword arguments you
give ``callOnFlip`` are passed through to ``send_event()``.

``ns.send_event(...)``
^^^^^^^^^^^^^^^^^^^^^^

Sends one event marker. ``event_type`` is the only required argument and
must be exactly four ASCII characters.

.. code-block:: python

    ns.send_event(event_type='resp')

    ns.send_event(
        event_type='resp',
        label='key r',                       # short name, up to 256 chars
        desc='key=r incorrect target=p',     # free text, up to 256 chars
        data={'trl_': 7, 'corr': False},     # keys exactly 4 chars
    )

It timestamps the moment it is *called*, then returns in microseconds
while a background thread does the network write. So calling it inside a
flip callback marks the flip, and calling it from your trial loop marks
that line of code — both are equally precise. Returns ``None``; pass
``wait=True`` only if you need the amplifier's reply.

.. _manual-drift-sampling:

Optional: only after disabling background sampling
---------------------------------------------------

This section applies only if you deliberately connected with
``auto_drift_background=False``.  With the default ``True`` setting, the
background sampler already does this work and you should continue at
:ref:`psychopy-cleanup`.

``ns.sample_drift_if_due(available_pause=...)``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Background sampling already covers this for you. You only need this
call if you have opted out of it with ``auto_drift_background=False``,
to get explicit control over exactly when NTP queries happen. It takes
a sample, but only if one is due and the gap you offer is long enough.
Call it during a quiet stretch — an inter-trial interval, a fixation, a
rest screen.

.. code-block:: python

    ns.connect(ntp_ip='10.10.10.51', auto_drift_background=False)
    ...
    ns.sample_drift_if_due(available_pause=1.0)

``available_pause`` is how much idle time you can safely give up, in
seconds. A sample blocks for roughly 170 ms, so this is what keeps NTP
queries away from your flips.

.. important::

   With ``auto_drift_background=False``, this call — or
   ``sample_drift()`` below — is **the only thing that ever takes a
   sample.** Skip both and the drift model never gets any data, so
   correction never engages, and nothing will interrupt your experiment
   to tell you. This is exactly the failure mode background sampling
   exists to remove.

``ns.sample_drift()``
^^^^^^^^^^^^^^^^^^^^^

Takes a sample **right now**, ignoring the schedule and asking no
questions about whether there is room for it. Blocks for roughly 170 ms
and returns the sample.

One call produces one sample, not four: the burst makes
``drift_samples`` NTP queries and keeps only the lowest-delay reply,
discarding the rest. Raising ``drift_samples`` therefore buys a *better*
sample rather than more of them.

.. code-block:: python

    sample = ns.sample_drift()

Use it when *you* know the moment is safe and you do not want the
schedule deciding — during instructions, a rest break, or a between-block
screen. Never call it inside a flip callback or a tight stimulus loop.

The most useful case is warming the model up before the first trial.
Drift correction needs 13 samples spanning 180 seconds by default, so an
experiment that starts cold spends its first few minutes uncorrected. A
short loop while the participant reads the instructions fixes that:

.. code-block:: python

    ns.begin_rec()          # must come first: this is what syncs the clock

    # Now warm the model up while the instructions are on screen.
    show_instructions()
    for _ in range(13):
        ns.sample_drift()
        core.wait(15.0)
    wait_for_keypress()

    # Trials start with correction already engaged.

That is 13 samples spanning 195 seconds, which clears the defaults of 13
samples over 180 seconds with a little room to spare.

.. warning::

   Do not compress the warm-up into a shorter window by sampling faster.
   ``drift_min_span`` gates the fit at whatever value it holds, so with
   the 180 s default, 40 samples crammed into 60 seconds produce **no fit
   at all** — 160 NTP queries and nothing to show.

Above that gate, span buys more than count. The uncertainty in the fitted
slope falls as ``1/span`` but only as ``1/sqrt(n)``, so stretching the
warm-up is worth about twice as much as sampling twice as often within
it. Simulated with realistic NTP noise:

.. list-table::
   :header-rows: 1
   :widths: 42 20 38

   * - Warm-up
     - Engages?
     - Slope error
   * - 13 samples over 60 s
     - **no**
     - never fits
   * - 13 samples over 195 s
     - yes
     - 5.2 ms/hour
   * - 26 samples over 195 s
     - yes
     - 3.3 ms/hour
   * - 13 samples over 390 s
     - yes
     - 2.6 ms/hour

``drift_min_span`` is settable — ``set_drift_requirements(min_span=...)``
— and lowering it will let a short window fit. It does not make the fit
any better, though: with the same 13 samples over 195 s, a lowered
threshold gives exactly the same 5.2 ms/hour. Accuracy comes from the
span you actually sample over; the threshold only decides whether the
model is allowed to engage.

What the default is protecting you from is a slope estimated over too
short a lever arm. At a 30-second span the slope error is about
33 ms/hour — larger than the roughly 17 ms/hour of drift being corrected,
so the correction would leave timing *worse* than doing nothing. The two
break even near a 60-second span, and the 180-second default keeps about
3x margin above that.

None of this is worth optimising hard. A 5 ms/hour slope error is about
1.3 ms of accumulated timing error over the following quarter hour, and
it shrinks as real samples arrive during the run. Thirteen samples over
195 seconds is a sensible floor, not a target.

.. note::

   ``sample_drift()`` is only available after ``begin_rec()``, because
   the NTP sync it performs is what establishes the clock the samples are
   measured against. Calling it earlier raises ``RuntimeError``.

Rule of thumb: leave sampling to the background thread unless you have a
specific reason not to. If you do, use ``sample_drift_if_due()`` in your
trial loop, where the schedule should decide; ``sample_drift()`` outside
it — a warm-up loop, a rest break — where you decide.

.. _psychopy-cleanup:

Cleanup
-------

``ns.end_rec()``
^^^^^^^^^^^^^^^^

Stops the recording. Any events still queued are flushed first, so
markers sent on the last trial still arrive.

.. code-block:: python

    ns.end_rec()

``ns.disconnect()``
^^^^^^^^^^^^^^^^^^^

Closes the connection and stops the background threads. Also flushes
queued events, and warns if drift sampling never happened.

.. code-block:: python

    ns.disconnect()

Put both of these in a ``finally`` block so they run even if the
experiment raises or the participant quits early:

.. code-block:: python

    recording = False
    try:
        ns.begin_rec()
        recording = True
        ...
    finally:
        if recording:
            ns.end_rec()
        ns.disconnect()

Default setup at a glance
-------------------------

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Call
     - When
   * - ``ns.connect(...)``
     - Once, at startup.
   * - ``ns.begin_rec()``
     - Once, before any events.
   * - ``win.callOnFlip(ns.send_event, ...)``
     - Every stimulus onset you want marked.
   * - ``ns.send_event(...)``
     - Responses, trial boundaries, anything not tied to a flip.
   * - ``ns.end_rec()``
     - Once, at the end.
   * - ``ns.disconnect()``
     - Once, after ``end_rec()``.

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

Rapid designs and the display beat
----------------------------------

This one is not about the package at all — the package never sees a frame
— but it can put a clean 16.7 ms error into your data, and it is easy to
mistake for a timing bug in the marker path. Anyone running short
inter-stimulus intervals should know about it.

Two rhythms that almost match
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Your display refreshes on a fixed heartbeat. Your experiment asks for a
stimulus on its own schedule. If those two do not divide evenly, the
request creeps through the refresh cycle a little more each trial.

A real measurement: a display running at 60.00043 Hz — 7 parts per
million fast — gives a frame period of 16.6665 ms. A 3.000 s interval is
then 179.9987 frames, not 180. Each trial the request arrives 0.0013 of a
frame earlier, about 22 microseconds.

That creep is harmless in itself. What is not harmless is that a display
can only show something **at a frame boundary**, so the outcome is
binary: the request either makes this frame or waits for the next one::

    frame:   |---------------|---------------|---------------|
                           ^                              ^
                      lands just before              lands just after
                      -> shown here                  -> shown 16.7 ms later

So a 22-microsecond-per-trial creep eventually produces a **one-frame
step**, appearing all at once, with nothing in the software looking any
different. In the run where this was measured, the marker-to-photocell
offset jumped 17 ms at 16 minutes and dropped back at 57 minutes — 40.8
minutes apart, against a predicted beat period of 39 minutes. Across the
step, consecutive trials had identical flip intervals, identical event
timestamps, and identical drift-model state.

Why rapid designs are more exposed
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Two reasons, and they compound.

**You cross the boundary more often.** The phase creeps by a fixed
fraction of a frame *per trial*, so more trials per hour means a faster
sweep. Halving the interval halves the beat period and doubles the number
of crossings in a session.

**A frame is a bigger share of what you are measuring.** 16.7 ms is 0.6%
of a 3-second interval but 8% of a 200 ms one, and it is comparable to
the latency separating many ERP components. The same absolute error is
far more damaging in a fast design.

Is your schedule vulnerable?
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Divide each interval by the frame period and see how close it lands to a
whole number:

.. code-block:: python

    frame_period = 1.0 / win.getActualFrameRate()
    for isi in set(my_intervals):
        frames = isi / frame_period
        slip = abs(frames - round(frames))
        if slip > 1e-9:
            print(f'{isi:g} s = {frames:.4f} frames, '
                  f'beat every {(1 / slip) * isi / 60:.0f} min')

Note that measuring the refresh matters. At *exactly* 60.000 Hz every
common interval is a whole number of frames and there is no beat at all.
The problem only appears once you use the real number.

The fix: count frames, not seconds
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The beat exists because there are two rhythms. Schedule in frames and
there is only one, so the request lands at the same point in the refresh
cycle every time and there is no boundary to cross.

Instead of waiting on a clock:

.. code-block:: python

    while clock.getTime() < next_onset:     # phase sweeps
        win.flip()

count the frames you have actually drawn:

.. code-block:: python

    isi_frames = max(1, round(isi / frame_period))
    onset_frame += isi_frames
    while frame < onset_frame - 1:          # phase is constant
        win.flip()
        frame += 1

    stim.draw()
    win.callOnFlip(ns.send_event, event_type='stm+')
    win.flip()
    frame += 1

``example5_psychopy_photocell_drift.py`` does this by default and logs the
measured refresh, the frame period, and each trial's ``onset_frame``.
``--clock-timing`` restores the old behaviour for comparison. In
simulation against a 60.00043 Hz display, clock timing reproduces the
sawtooth exactly — onset lateness sweeping a full frame, standard
deviation 4.6 ms — while frame counting holds it flat.

What it costs
^^^^^^^^^^^^^

Two trade-offs worth accepting knowingly.

**Your interval is no longer exactly nominal.** 180 frames at 16.6665 ms
is 2.99997 s, not 3.000 s. A schedule 30 microseconds off nominal but
perfectly stable is almost always better than one that is exactly nominal
and occasionally a whole frame wrong.

**Dropped frames accumulate.** A ``flip()`` that spans two refreshes still
counts as one, so the schedule slips permanently rather than
self-correcting the way clock-based waiting does. If absolute schedule
matters more to you than stimulus-to-stimulus consistency, that is a
reason to prefer the clock — but for most designs consistency is what you
want.

.. note::

   The association between the phase sweep and the one-frame step is
   strong — matching magnitude, matching period — but the mechanism
   linking them has not been isolated. ``win.flip()`` already blocks until
   vsync, so something further down the presentation path has to be
   involved. Frame counting removes the sweep for certain; whether that
   removes the step is being tested. If your own logs show onset lateness
   pinned flat but a one-frame step persisting, the cause is downstream of
   the flip — the compositor, the photocell threshold, or the amplifier.

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
