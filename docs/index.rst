EGI PyNetStation
================

.. admonition:: Independent community project
   :class: important

   This is not an EGI or MagStim EGI product. ``egi-pynetstation`` is an
   independent, community-driven open-source effort that enables Python
   experiments to synchronize event timing with EGI amplifiers. It is
   maintained by community contributors, not by EGI or MagStim EGI.

``egi-pynetstation`` sends precisely timed ECI event markers from Python to
EGI Net Station / Amp Server Pro.

Most experiments need five commands:

.. code-block:: python

   from egi_pynetstation import NetStation

   ns = NetStation('10.10.10.42', 55513)
   ns.connect(ntp_ip='10.10.10.51')
   ns.begin_rec()
   ns.send_event(event_type='stim')
   ns.end_rec()
   ns.disconnect()

Drift correction and background sampling are automatic. Validate the complete
setup with the timing test before collecting data.

Basics
------

Read these pages in order. They cover the normal experiment path without the
implementation details.

.. toctree::
   :maxdepth: 2
   :caption: Basics

   installation
   quickstart
   psychopy
   timing_test
   examples

Advanced and reference
----------------------

The advanced guide covers tuning, manual sampling, timestamp internals,
diagnostics, platform behavior, and validation.

.. toctree::
   :maxdepth: 2
   :caption: Advanced and reference

   advanced
   api
   legacy

Indices
-------

* :ref:`genindex`
* :ref:`modindex`
