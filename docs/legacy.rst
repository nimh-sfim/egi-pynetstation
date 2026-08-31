Running version 1.x
===================

New experiments should use the current release. This page exists only for
labs that must reproduce an older environment.

Install the last 1.x release
----------------------------

Use a separate virtual environment so an old dependency set does not affect
new experiments:

.. code-block:: bash

   python -m pip install "egi-pynetstation==1.0.1"

What changed
------------

Version 1.x used wall-clock timestamps, blocking event sends, and manual clock
resynchronization. The current package instead captures a high-resolution
monotonic timestamp immediately, sends events in the background, and models
clock-rate drift continuously.

Migrate an experiment
---------------------

The normal recording calls remain familiar:

.. code-block:: python

   ns.connect(ntp_ip=amp_ip, drift_warmup=True)
   ns.begin_rec()
   ns.send_event(event_type='stim')
   ns.end_rec()
   ns.disconnect()

Remove periodic ``ntpsync()`` calls from the recording. ``begin_rec()``
performs the required sync, and the drift model maintains alignment without
changing the recording epoch. PsychoPy experiments should schedule visual
markers with ``win.callOnFlip()``.

See :doc:`quickstart` for the current workflow and :ref:`advanced-drift` for
the clock model in detail.
