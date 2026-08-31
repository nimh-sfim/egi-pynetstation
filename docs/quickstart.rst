Quickstart
==========

The normal lifecycle
--------------------

.. code-block:: python

   from egi_pynetstation import NetStation

   ns = NetStation('10.10.10.42', 55513)

   ns.connect(ntp_ip='10.10.10.51')
   try:
       ns.begin_rec()
       try:
           ns.send_event(event_type='stim')
           ns.send_event(
               event_type='resp',
               label='space',
               data={'trl_': 1, 'rt__': 0.482},
           )
       finally:
           ns.end_rec()
       print(ns.session_summary())
   finally:
       ns.disconnect()

Replace the three network values with those configured for your lab.

The five commands
-----------------

``connect(ntp_ip=...)``
   Opens ECI. Drift correction and background sampling start automatically.

``begin_rec()``
   Starts one Net Station recording and performs its NTP clock sync.

``send_event(event_type='stim')``
   Captures the current timestamp and queues a marker. ``event_type`` must be
   exactly four ASCII characters.

``end_rec()``
   Flushes pending markers and ends the current recording.

``disconnect()``
   Closes ECI and stops background workers.

Event fields
------------

Only ``event_type`` is required. Common optional fields are:

.. code-block:: python

   ns.send_event(
       event_type='resp',
       label='correct',
       desc='participant pressed space',
       data={'trl_': 12, 'corr': True, 'rt__': 0.482},
   )

Every ``data`` key must also be exactly four characters. Values may be
``bool``, ``int``, ``float``, or ``str``.

Multiple recordings, one connection
-----------------------------------

End one recording before starting the next:

.. code-block:: python

   ns.begin_rec()
   # session 1
   ns.end_rec()

   ns.begin_rec()
   # session 2; the stable drift evidence is retained
   ns.end_rec()

Do not call ``ntpsync()`` yourself during a recording. ``begin_rec()`` performs
the sync each recording needs.

Check the result
----------------

.. code-block:: python

   summary = ns.session_summary()
   if not summary['ok']:
       print(summary)

For a persistent diagnostic log:

.. code-block:: python

   ns = NetStation(ip, port, error_log='run_errors.jsonl')

PsychoPy users should continue with :doc:`psychopy`, then run the
:doc:`timing_test`. Implementation and tuning details are in :doc:`advanced`.
