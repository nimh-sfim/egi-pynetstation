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

The short version
-----------------

.. code-block:: python

    from egi_pynetstation import NetStation

    ns = NetStation('10.10.10.42', 55513)
    ns.connect(
        ntp_ip='10.10.10.51',
        auto_drift_interval=15.0,
    )
    ns.begin_rec()

    # In a visual experiment, mark the flip that shows the stimulus.
    win.callOnFlip(ns.send_event, event_type='stm+')
    win.flip()

    # In an inter-trial interval, let the clock model stay current.
    ns.sample_drift_if_due(available_pause=1.0)

    ns.end_rec()
    ns.disconnect()

Three things are worth knowing before you write anything else:

1. **Drift correction and the sampling schedule are both on by default,
   but something still has to take the samples.** Either call
   :meth:`~egi_pynetstation.NetStation.NetStation.sample_drift_if_due`
   during quiet periods, or pass ``auto_drift_background=True`` and let
   the package handle it on its own thread. See :doc:`drift`.
2. **Send events from the flip callback.**
   :meth:`~egi_pynetstation.NetStation.NetStation.send_event` never
   blocks, so it is safe there. See :doc:`psychopy`.
3. **Do not re-sync the ECI clock during a recording.** One sync at
   ``begin_rec()`` is correct; repeated syncs reset the timestamp epoch.

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
