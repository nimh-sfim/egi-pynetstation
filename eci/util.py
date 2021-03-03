#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Utilities for the ECI module"""

import sys
from math import modf
from typing import Union
from .exceptions import *

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


def get_ntp_byte(number: Union[float, int, bytes]) -> bytes:
    """Converts numbers or bytes into an NTP format

    Parameters
    ----------
    number: the number of seconds in the NTP epoch

    Returns
    -------
    The byte array representing the time in NTPv4 format (8 bytes)

    Raises
    ------
    NTPException for invalid input; one of several types
    NTPInvalidType for passing a value that is not a number or byte array
    NTPInvalidByte if the byte array is not of length 8
    OverflowError if the number of seconds cannot be represented as 4 bytes
    """
    # Placeholders as we build the NTP 
    part_1 = sys_to_bytes(0, 4)
    part_2 = sys_to_bytes(0, 4)
    # Numbers to use for splitting a float into two integers
    second_portion = 0
    subsecond_portion = 0
    # Constant representation of sub-integer resolution
    float_res = 2**-32
    if isinstance(number, int):
        # Convert number of seconds to 32-bit int with 2 0-bytes
        part_1 = sys_to_bytes(number, 4)
        return part_1 + part_2
    if isinstance(number, float):
        # Split number into two parts and build NTP bytestr
        subsecond_portion, second_portion = modf(number)
        part_1 = sys_to_bytes(second_portion, 4)
        part_2 = sys_to_bytes(round(subsecond_portion/float_res), 4)
        return part_1 + part_2
    elif isinstance(number, bytes):
        if len(number) == 8:
            return number
        else:
            raise NTPInvalidByte(number)
    else:
        raise NTPInvalidType(number)
