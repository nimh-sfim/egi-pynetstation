#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Convenience shim for running the clock check from a repository checkout.

The real implementation ships inside the package, so users who installed
from PyPI can run it too:

    python -m egi_pynetstation.check_clocks
"""

from egi_pynetstation.check_clocks import main

if __name__ == '__main__':
    raise SystemExit(main())
