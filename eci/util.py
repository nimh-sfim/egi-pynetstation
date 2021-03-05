#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Utilities for the ECI module"""

import sys
from math import modf
from typing import Union
from .exceptions import *

ntp_res = 2**-32


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


def sys_from_bytes(bytearr: bytes) -> int:
    """Simplified from_bytes that always uses sys.byteorder

    Parameters
    ----------
    bytearr: the byte representation of the integer

    Returns
    -------
    The integer representation of the bytes using system byte order
    """
    return int.from_bytes(bytearr, sys.byteorder)


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
    OverflowError if the number of seconds cannot be represented
    """
    # Placeholders as we build the NTP
    part_1 = sys_to_bytes(0, 4)
    part_2 = sys_to_bytes(0, 4)
    # Numbers to use for splitting a float into two integers
    second_portion = 0
    subsecond_portion = 0
    if isinstance(number, int):
        # Convert number of seconds to 32-bit int with 2 0-bytes
        part_1 = sys_to_bytes(number, 4)
        return part_1 + part_2
    if isinstance(number, float):
        # Split number into two parts and build NTP bytestr
        subsecond_portion, second_portion = modf(number)
        part_1 = sys_to_bytes(second_portion, 4)
        part_2 = sys_to_bytes(round(subsecond_portion / ntp_res), 4)
        return part_1 + part_2
    elif isinstance(number, bytes):
        if len(number) == 8:
            return number
        else:
            raise NTPInvalidByte(number)
    else:
        raise NTPInvalidType(number)


def get_ntp_float(bytearr: bytes) -> float:
    """Converts an NTP byte array into the number of seconds in NTP epoch

    Parameters
    ----------
    bytearr: the byte array representing the NTPv4 time

    Returns
    -------
    The number of seconds in the NTP epoch

    Raises
    ------
    NTPException for invalid input; one of several types
    NTPInvalidByte if the byte array is the incorrect size
    NTPInvalidType if the input isn't a byte array
    """
    # Placeholders as we deconstruct NTP
    part_1 = sys_to_bytes(0, 4)
    part_2 = sys_to_bytes(0, 4)
    # Numbers to add when decoding
    second_portion = 0
    subsecond_portion = 0.0
    if isinstance(bytearr, bytes):
        if len(bytearr) == 8:
            part_1 = bytearr[0:4]
            part_2 = bytearr[4:]
            second_portion = sys_from_bytes(part_1)
            subsecond_portion = sys_from_bytes(part_2) * ntp_res
            return second_portion + subsecond_portion
        else:
            raise NTPInvalidByte(bytearr)
    else:
        raise NTPInvalidType(bytearr)
