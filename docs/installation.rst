Installation
============

From PyPI
---------

.. code-block:: bash

    pip install egi-pynetstation

From GitHub
-----------

.. code-block:: bash

    git clone https://github.com/nimh-sfim/egi-pynetstation.git
    cd egi-pynetstation
    pip install .


With PsychoPy
-------------

You can install directly into `PsychoPy <https://psychopy.org>`_,
in the standalone package without any further downloads or steps.
We are preparing a plugin to make PsychoPy builder setups easier.

.. _check-your-clocks:

Check your clocks first
-----------------------

The drift corrector assumes sub-millisecond resolution from both
``time.time()`` and ``time.monotonic()``. Some Python and Windows
combinations provide roughly 15.6 ms instead, which degrades timing
silently rather than raising an error.

Run this once on any new stimulus computer:

.. code-block:: bash

    python -m egi_pynetstation.check_clocks

It reports the measured resolution of each clock, ``time.sleep()``
overshoot, and the jitter in the system-versus-monotonic clock
difference. A healthy machine looks like this::

    time          impl=clock_gettime(CLOCK_REALTIME)
                  claimed=1.000e-06 s  measured=7.153e-07 s  monotonic=False
    monotonic     impl=mach_absolute_time()
                  claimed=4.167e-08 s  measured=4.100e-08 s  monotonic=True

    system-monotonic skew jitter over 2000 reads: 0.0019 ms

    Clocks look suitable for drift-corrected ECI timing.

.. note::

   **On Windows, Python 3.13 or newer is strongly recommended.** Earlier
   CPython versions used ``GetTickCount64()`` for ``time.monotonic()``
   and ``GetSystemTimeAsFileTime()`` for ``time.time()``, both with
   ~15.6 ms resolution.

   That matters more than it first appears: ``ntplib`` computes its
   offset from ``time.time()`` internally, so on an affected machine the
   raw NTP measurement itself carries ~15.6 ms quantization (about
   4.5 ms RMS). Simulated over an hour, that produces roughly **12 ms**
   of correction error and repeated fit rejections — the model still
   engages, so it looks like it is working.

   On pre-3.13 Python these two clocks are updated on the *system timer
   tick*, so the resolution you actually get depends on whether anything
   has raised it. At a 1 ms tick the same simulation gives 0.1 ms error.

   .. warning::

      **PsychoPy does not raise the Windows timer resolution.** Its only
      Windows timing lever is ``rush()``, which sets thread and process
      priority. Do not assume importing PsychoPy fixes this.

   .. note::

      ``timeBeginPeriod()`` is **not** system-wide on current Windows.
      Since Windows 10 version 2004 the request applies to the calling
      process only, and on Windows 11 a process whose window is
      minimised or occluded can silently lose the resolution it asked
      for. Two consequences: call it from your own experiment process
      rather than relying on another program having raised it, and keep
      the stimulus window in the foreground for the whole run.

      Trust the *measured* ``time.time()`` and ``time.monotonic()``
      resolutions over the nominal timer setting. They are what the
      drift corrector actually experiences.

   To find out what your machine actually does:

   .. code-block:: bash

       python -m egi_pynetstation.check_clocks --compare

   That measures the clocks as launched, after importing PsychoPy, and
   after calling ``timeBeginPeriod(1)``, and reads the timer tick
   directly. If raising the tick is what helps, call it yourself at the
   start of your experiment:

   .. code-block:: python

       import ctypes, platform
       if platform.system() == 'Windows':
           ctypes.WinDLL('winmm').timeBeginPeriod(1)   # timeEndPeriod(1) at exit

Keeping the machine awake
-------------------------

On macOS, wrap your experiment so the display and the system cannot
sleep:

.. code-block:: bash

    caffeinate -dis python my_experiment.py

``-d`` prevents display sleep, ``-i`` prevents idle sleep, and ``-s``
prevents system sleep. When a utility is given, the assertions are held
for exactly that process's lifetime.

This matters for more than the screensaver. Python's ``time.monotonic()``
on macOS does not advance while the machine is asleep, so a sleep
mid-recording would corrupt the elapsed-time baseline that every event
timestamp is built on.

Also disable the screen saver explicitly — the display-sleep assertion is
not documented to suppress it — and make sure no password-on-wake lock
can interrupt a run.

Prefer AC power. On battery, ``-s`` is silently ignored, Low Power Mode
alters timer coalescing, and the scheduler leans harder on efficiency
cores.
