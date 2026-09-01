:orphan:

Drift correction
================

The user-facing setup is one option:

.. code-block:: python

   ns.connect(ntp_ip=amp_ip, drift_warmup=True)

Sampling and correction otherwise run automatically. The complete explanation,
settings table, manual-sampling workflow, and monitoring behavior have moved to
:ref:`advanced-drift`.
