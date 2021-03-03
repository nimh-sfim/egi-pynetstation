#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Utilities for the ECI module"""

import sys

def sys_to_bytes(number: int, size: int) -> bytes:
    """Simplified to_bytes that always uses sys.byteorder

    Parameters
    ----------
    number: the number to turn into bytes
    size: the size in bytes to use for the representation

    Returns
    -------
    The byte string representation of the number
    """
    return int(number).to_bytes(size, sys.byteorder)
