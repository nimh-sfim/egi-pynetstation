Using PsychoPy
==============

Mark a visual onset
-------------------

Register the event before the flip that presents the stimulus:

.. code-block:: python

   stimulus.draw()
   win.callOnFlip(ns.send_event, event_type='stim')
   win.flip()

``send_event()`` captures the flip time immediately, then a background worker
sends the marker. Network latency does not move the event timestamp.

Minimal experiment
------------------

.. code-block:: python

   from psychopy import core, visual
   from egi_pynetstation import NetStation

   win = visual.Window(fullscr=True, color='black')
   stimulus = visual.TextStim(win, text='GO')
   ns = NetStation('10.10.10.42', 55513)

   ns.connect(ntp_ip='10.10.10.51')
   try:
       ns.begin_rec()
       try:
           stimulus.draw()
           win.callOnFlip(ns.send_event, event_type='stim')
           win.flip()
           core.wait(1.0)
       finally:
           ns.end_rec()
   finally:
       ns.disconnect()
       win.close()

What belongs in a flip callback
-------------------------------

Keep callbacks short. These are safe:

* ``ns.send_event(...)``
* ``ns.capture_time()``
* assigning a small value to an existing object

Do not query NTP, wait for a response, write files, or run analysis inside the
callback. In particular, do not pass ``wait=True`` to ``send_event()`` there.

Schedule in frames
------------------

For frame-locked visual experiments, express durations and inter-stimulus
intervals as whole frames. A seconds-based schedule can cross a refresh
boundary and produce a full-frame presentation step even when event timing is
correct.

The photocell example records frame intervals and flags long frames. Run it as
described in :doc:`timing_test`.

More detail
-----------

Next, validate the actual computer, display, amplifier, and photocell path in
:doc:`timing_test`. See :ref:`advanced-psychopy` for captured timestamps,
frame diagnostics, manual sampling windows, and platform behavior.
