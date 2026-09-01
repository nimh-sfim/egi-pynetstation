Timing Test
===========

Run a timing test before collecting data on a new setup. It checks the clocks
first, then measures the complete path from PsychoPy through Net Station to a
photocell.

Check the clocks
----------------

From a repository checkout, run:

.. code-block:: bash

   python check_clocks.py

If the package is installed elsewhere, the equivalent command is:

.. code-block:: bash

   python -m egi_pynetstation.check_clocks

The last line should be:

.. code-block:: text

   egi-pynetstation clocks look suitable for drift-corrected ECI timing.

The ``egi wall`` and ``egi monotonic`` rows should both have measured
resolution below 1 ms. Older Python versions on Windows may also report coarse
standard Python clocks; that is acceptable when the two ``egi`` clocks are
sub-millisecond and the final verdict says they are suitable.

On Windows, this comparison provides more detail:

.. code-block:: bash

   python check_clocks.py --compare

It shows the clocks as launched, after importing PsychoPy, and after requesting
a finer Windows timer resolution. A ``PROBLEMS FOUND`` result or a coarse
``egi-pynetstation`` row should be resolved before collecting data.

Run the photocell test
----------------------

Place the photocell over the white target and run Example 5 from the repository
checkout. Start with the normal drift model so the test shows the setup's
unassisted startup behavior:

.. code-block:: bash

   python example5_psychopy_photocell_drift.py amp --sessions 2 --duration 600 --fullscreen --log timing.csv --error-log timing_errors.jsonl --frame-interval-log frames.csv

Each session lasts 600 seconds. Both are separate Net Station recordings on
one ECI connection, so this also checks the transition between recordings.
Use ``custom`` with ``--ip-cmd``, ``--ip-clock``, and ``--port`` if the lab
does not use the standard ``amp`` addresses.

PsychoPy Coder users can instead run
``example5_psychopy_photocell_drift_gui.py``. For the initial baseline, uncheck
``Short model first, then stable long model`` in its dialog.

Compare each ``stm+`` marker with the corresponding photocell edge in Net
Station or its exported data. Look at the offset at the beginning and end of
each recording, its range and trend, any abrupt steps, and whether outliers
coincide with long frames in ``frames.csv``. Keep the photocell export with the
three generated logs.

If startup drift is visible
---------------------------

Our test systems have behaved differently. The macOS recordings showed little
drift before the stable model engaged, while the Windows test bed showed much
larger initial drift and variability. That is a reason to test each setup, not
an assumption that every computer running the same operating system will act
the same way.

There are two ways to cover the first 180 seconds:

1. Connect earlier. Background sampling starts at ``connect()``, and those
   samples are retained when ``begin_rec()`` establishes the recording clock.
   Participant and cap setup can therefore provide the stable model's required
   baseline before the experiment begins. To exercise this in Example 5, add
   ``--prep 240``.
2. Enable the short warmup model. In an experiment, use:

   .. code-block:: python

      ns.connect(ntp_ip=amp_ip, drift_warmup=True)

   In Example 5, add ``--staged-drift``. The warmup model samples faster and
   can engage after about 20 seconds. The stable 180-second model takes over
   automatically when ready. This substantially reduced the early drift on
   the Windows test bed while preserving the stability of the long model.

Repeat the timing test after choosing either approach and confirm the result
with the photocell. More interpretation and tuning options are in
:ref:`advanced-validation`.

Every experiment should have a timing test performed at least once to measure
the offsets for that particular setup on those specific computers.
