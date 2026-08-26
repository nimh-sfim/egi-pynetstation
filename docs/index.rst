Welcome to egi-pynetstation's documentation!
============================================

``egi-pynetstation`` is a Python interface for sending ECI commands and
event markers to EGI Net Station / Amp Server Pro, designed for
high-resolution event marking from a small API.

A single ECI ``NTPClockSync`` establishes the event timestamp epoch, and
client-side drift correction then compensates for the slow clock drift
between the stimulus computer and the amplifier's NTP server.

Validated over one-hour continuous recordings against a photocell: the
marker-to-photocell offset held to a standard deviation of **0.94 ms**
with a residual trend of **+0.49 ms/hour**, across a run in which the
operating system stepped the system clock by 256 ms.

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   installation
   quickstart
   psychopy
   drift
   diagnostics
   examples
   api
   legacy

The short version
-----------------

.. code-block:: python

    from egi_pynetstation import NetStation

    ns = NetStation('10.10.10.42', 55513)
    ns.connect(ntp_ip='10.10.10.51')   # drift sampling starts on its own thread
    ns.begin_rec()

    # In a visual experiment, mark the flip that shows the stimulus.
    win.callOnFlip(ns.send_event, event_type='stm+')
    win.flip()

    ns.end_rec()
    ns.disconnect()

Three things are worth knowing before you write anything else:

1. **Drift correction is on by default, and so is sampling for it** — a
   background thread takes NTP samples on its own, so there is nothing to
   wire up. Advanced use cases can take over sampling manually instead;
   see :doc:`drift`.
2. **Send events from the flip callback.**
   :meth:`~egi_pynetstation.NetStation.NetStation.send_event` never
   blocks, so it is safe there. See :doc:`psychopy`.
3. **Do not re-sync the ECI clock during a recording.** One sync included in
   ``begin_rec()`` is correct; repeated syncs reset the timestamp epoch and
   are refused unless ``force=True`` is passed for diagnostics.

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
