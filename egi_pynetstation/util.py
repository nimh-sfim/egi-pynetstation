#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Utilities for the ECI module"""

import sys
from math import modf
from typing import Union
from struct import pack
from time import strftime, localtime
from datetime import datetime, timezone
from .exceptions import *

ntp_res = 2**-32
ntp_epoch = datetime(1900, 1, 1, tzinfo=timezone.utc)
unix_epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)


def sys_to_bytes(number: int, size: int, signed: bool = False) -> bytes:
    """Simplified to_bytes that always uses sys.byteorder

    Parameters
    ----------
    number: the integer to turn into bytes
    size: the size in bytes to use for the representation
    signed: whether the integer should be signed

    Returns
    -------
    The byte string representation of the number
    """
    return int(number).to_bytes(size, sys.byteorder, signed=signed)


def sys_from_bytes(bytearr: bytes, signed: bool = False) -> int:
    """Simplified from_bytes that always uses sys.byteorder

    Parameters
    ----------
    bytearr: the byte representation of the integer
    signed: whether the integer to read is signed

    Returns
    -------
    The integer representation of the bytes using system byte order
    """
    if not isinstance(bytearr, bytes):
        t_bytearr = type(bytearr)
        raise TypeError(
            f'bytearr is type {t_bytearr}, should bytes'
        )
    return int.from_bytes(bytearr, sys.byteorder, signed=signed)


def get_ntp_byte(number: Union[float, int, bytes]) -> bytes:
    """Converts numbers or bytes into an NTP format.

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

    Notes
    -----
    Tested for little-endian only.
    """
    second_portion = 0
    subsecond_portion = 0
    if isinstance(number, int):
        # Convert number of seconds to 32-bit int with 2 0-bytes
        second_portion = number
    elif isinstance(number, float):
        # Split number into two parts and build NTP bytestr
        subsecond_portion, second_portion = modf(number)
        subsecond_portion /= ntp_res
        second_portion = int(second_portion)
        subsecond_portion = int(subsecond_portion)
    elif isinstance(number, bytes):
        if len(number) == 8:
            return number
        else:
            raise NTPInvalidByte(number)
    else:
        raise NTPInvalidType(number)
    return pack('II', second_portion, subsecond_portion)


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


def format_time(time: float) -> str:
    """Format the float time in a human readable format

    Parameter
    ---------
    time: float
        Floating representation of a time, from time.time()

    Returns
    -------
    A string representation of the time in human-readable format
    """
    subseconds, seconds = modf(time)
    if subseconds < 0:
        raise ValueError('Something strange has occurred')
    subseconds *= 1e6
    subseconds = int(subseconds)
    fmt = '%Y-%m-%d:%I:%M%:%S'
    return strftime(fmt, localtime(time)) + '.' + str(subseconds)
