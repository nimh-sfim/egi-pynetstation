Quickstart
==========

Every session follows the same shape: construct, connect, record, send
events, stop.

.. code-block:: python

    from egi_pynetstation import NetStation

    # The computer running Net Station, and the ECI port it listens on.
    IP_ns = '10.10.10.42'
    port_ns = 55513
    # The amplifier, which is also the NTP server.
    IP_amp = '10.10.10.51'

    ns = NetStation(IP_ns, port_ns)
    ns.connect(ntp_ip=IP_amp)

    ns.begin_rec()

    ns.send_event(event_type='HIYA')

    # Events may carry a shallow data dictionary.
    ns.send_event(event_type='STIM', data={'dogs': 'fido'})

    ns.end_rec()
    ns.disconnect()

``begin_rec()`` performs the one ECI ``NTPClockSync`` that establishes the
event timestamp epoch, so it must come before any events.

Event format rules
------------------

``event_type`` is the only required argument. ``ns.send_event(event_type='stm+')``
is a complete, valid event. ``label``, ``desc``, and ``data`` are all
optional, and you can supply any combination of them when you want the
marker to carry more than its four-character code.

ECI is strict about field widths, and getting these wrong raises rather
than silently truncating:

* ``event_type`` must be **exactly four ASCII characters**.
* Every key in ``data`` must also be **exactly four characters**. This is
  why the examples use names like ``trl_``, ``key_``, ``rt__``.
* ``data`` values may be ``bool``, ``int``, ``float``, or ``str``.
* ``data`` must be flat — no nested dictionaries.
* ``label`` and ``desc`` are free text, up to 256 characters each.

.. code-block:: python

    ns.send_event(
        event_type='resp',
        label='key r',
        desc='key=r incorrect target=p',
        data={'trl_': 7, 'key_': 'r', 'corr': False, 'rt__': 0.482},
    )

Putting the human-readable outcome in ``desc`` is worth doing: it is
legible in Net Station without decoding anything, while ``data`` carries
the machine-readable version for analysis.

Timestamps
----------

``send_event()`` defaults to ``start='now'``, which timestamps the event
using :meth:`~egi_pynetstation.NetStation.NetStation.getTime` — elapsed
seconds since the NTP sync, with drift correction applied once the model
has engaged.

You can also pass an explicit ``start`` as a float, or convert a clock
reading you captured earlier:

.. code-block:: python

    import time

    captured = time.monotonic()          # e.g. inside a flip callback
    # ... later, off the critical path ...
    start = ns.time_at_monotonic(captured)
    ns.send_event(start=start, event_type='stm+')

.. important::

   **Use exactly one ECI clock sync per recording.**
   :meth:`~egi_pynetstation.NetStation.NetStation.ntpsync` is called for
   you by ``begin_rec()``. Calling it again to "keep the clock fresh"
   resets the local event timestamp epoch and creates a discontinuity in
   the timestamps sent to Net Station, so a second call now raises
   ``NetStationLifecycleError``. Pass ``force=True`` only for
   diagnostics, where re-basing the epoch is the thing being measured.

   For the same reason, one ``NetStation`` object records once. A second
   ``begin_rec()`` is refused: it would re-run the sync while the drift
   model still holds samples measured from the previous origin. Call
   ``disconnect()`` and build a new object for the next recording.

   Correcting for drift is what
   :meth:`~egi_pynetstation.NetStation.NetStation.sample_drift` is for.
   It queries NTP only and sends no ECI command. See :doc:`drift`.

Cleaning up
-----------

``end_rec()`` and ``disconnect()`` both flush any queued events first, so
markers sent on the last trial still reach Net Station. A ``finally``
block is the right home for them:

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

Next steps
----------

* :doc:`psychopy` — the integration pattern for visual experiments.
* :doc:`drift` — what drift correction does and how to feed it.
* :doc:`examples` — complete runnable scripts.
