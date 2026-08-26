Running version 1.x
===================

Version 2.0 changed how event timestamps are produced. If you have an
experiment written against 1.x, or you need to reproduce an analysis from
a recording made with it, you can keep running the old release — this
page covers how, and what its timing numbers mean.

For new work, use 2.0. Nothing here is a recommendation; it is a record
of what 1.x did, so that data collected with it can be interpreted
correctly.

Installing it
-------------

Version ``1.0.1`` is the last 1.x release and is on PyPI::

    pip install 'egi-pynetstation==1.0.1'

Pin it in the environment file for the experiment rather than installing
it globally, so a later ``pip install egi-pynetstation`` cannot silently
move a working paradigm onto 2.0:

.. code-block:: yaml

    dependencies:
      - pip:
          - egi-pynetstation==1.0.1

The matching source is the ``v1.0.1`` tag in the repository::

    git checkout v1.0.1

.. warning::

   Both releases install as the package ``egi_pynetstation`` and both
   report a ``__version__``, so the only way to tell which one an
   experiment actually imported is to ask it::

       import egi_pynetstation
       print(egi_pynetstation.__version__, egi_pynetstation.__file__)

   Worth printing at the top of every run. A repository checkout takes
   precedence over an installed copy when Python starts in that
   directory, so the two can differ between machines without anything
   looking wrong.

The 1.x API
-----------

Eleven public methods, all of them still present in 2.0 except the two
noted below:

.. code-block:: python

    from egi_pynetstation.NetStation import NetStation

    ns = NetStation('10.10.10.42', 55513)
    ns.connect(ntp_ip='10.10.10.51')
    ns.begin_rec()                    # performs the NTP sync itself
    ns.send_event(event_type='stm+')  # blocks until the amplifier answers
    ns.end_rec()
    ns.disconnect()

.. list-table::
   :header-rows: 1
   :widths: 34 66

   * - Method
     - Notes
   * - ``connect(clock=, ntp_ip=)``
     - Two arguments only; there is nothing to configure.
   * - ``begin_rec()``
     - Syncs **before** ``BeginRecording``. 2.0 reversed this order.
   * - ``ntpsync()``
     - Re-runs the ECI clock sync. 1.x allows this at any time.
   * - ``resync()``
     - Calls ``ntpsync()`` again. Deprecated in 2.0; see below.
   * - ``send_event(...)``
     - Blocking. No ``wait`` argument, no background sender.
   * - ``end_rec()``, ``disconnect()``, ``rec_start()``
     - Unchanged in behaviour.
   * - ``since_start()``
     - Removed in 2.0. Its own docstring said "DO NOT USE".
   * - ``resync_do_not_use_not_recommended()``
     - Removed in 2.0.

What 1.x does not do
--------------------

These are the reasons 2.0 exists. They matter when interpreting a
recording made with 1.x.

**No clock drift correction.** The stimulus computer and the amplifier
run on separate crystals whose rates differ by a few parts per million,
and 1.x measures that difference once, at the sync, and never again. The
resulting error grows for as long as the recording runs — on the order of
milliseconds per hour, in whichever direction that particular pair of
clocks happens to drift. 2.0 samples NTP continuously and corrects for
it; see :doc:`drift`.

**Event timestamps come from the system clock.** ``send_event(start='now')``
computes ``time.time() - syncepoch``. If the operating system adjusts the
clock mid-recording — an NTP daemon slewing or stepping it — every
subsequent event timestamp moves with it. 2.0 works in the monotonic
clock frame, which cannot be adjusted.

**Sends block.** ``send_event()`` writes to the socket and waits for the
reply on the calling thread. In a PsychoPy flip callback that stalls the
following frame, so 1.x experiments generally cannot mark the flip
directly. 2.0's non-blocking send exists for exactly this; see
:doc:`psychopy`.

**Re-syncing was the recommended practice.** 1.x documentation suggested
calling ``resync()`` periodically to keep the clock fresh. It is now
understood that each sync re-bases the event timestamp epoch, so
timestamps before and after are measured from different origins. 2.0
refuses a second sync unless ``force=True`` is passed. A 1.x recording
that called ``resync()`` during the run has a discontinuity at each call.

**A bad ``start`` value drops the event silently.** 1.x *returns* a
``TypeError`` object rather than raising it, so the exception is truthy,
no ``except`` clause fires, and no event is sent. 2.0 raises.

Moving to 2.0
-------------

Most 1.x scripts run unmodified. Three things need attention:

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - In 1.x
     - In 2.0
   * - ``ns.resync()`` during a recording
     - Remove it. Drift is corrected continuously.
   * - ``ns.ntpsync()`` after ``begin_rec()``
     - Remove it. ``begin_rec()`` performs the one sync needed, and a
       second call raises unless ``force=True``.
   * - ``ns.since_start()``
     - Use ``ns.rec_start()``, or ``ns.getTime()`` for an event
       timestamp.
   * - Checking the return of ``send_event()``
     - The default send is asynchronous and returns ``None``. Check
       ``ns.session_summary()`` at the end of the run instead, or pass
       ``wait=True`` for the old blocking behaviour.
