"""Make the repository root importable during tests.

The example scripts (e.g. ``example5_psychopy_photocell_drift``) live at the
repository root rather than inside the package, and a couple of tests import
them directly. Depending on how pytest is invoked, the root is not always on
``sys.path`` -- it is when pytest is run from the root in prepend-import mode,
but not under every CI invocation. Anchoring it here, next to the rootdir
pytest already discovers, makes those imports work regardless.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
