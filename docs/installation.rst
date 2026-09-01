Installation
============

PyPI
----

.. code-block:: bash

   pip install egi-pynetstation

Development checkout
--------------------

.. code-block:: bash

   git clone https://github.com/nimh-sfim/egi-pynetstation.git
   cd egi-pynetstation
   pip install -e .

Verify the copy Python imports:

.. code-block:: bash

   python -c "import egi_pynetstation; print(egi_pynetstation.__file__)"

PsychoPy
--------

Install the package into the same Python environment that runs PsychoPy.
Standalone PsychoPy users can install from PsychoPy's package manager or its
environment terminal.

Next
----

Continue with :doc:`quickstart`. You will check the computer clocks and the
complete marker path in :doc:`timing_test` before collecting data.
