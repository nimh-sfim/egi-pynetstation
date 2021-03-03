#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""ECI controls and returns"""

import sys
import math

from .exceptions import *
from .util import sys_to_bytes, get_ntp_byte


byte_table = {
    "Query": b"Q",
    "NewQuery": b"Y",
    "Exit": b"X",
    "BeginRecording": b"B",
    "EndRecording": b"E",
    "Attention": b"A",
    "ClockSync": b"T",
    "NTPClockSync": b"N",
    "NTPReturnClock": b"S",
    "EventData": b"D",
}

requires_data = ("Query", "ClockSync", "NTPClockSync", "EventData")

allowed_endians = ("NTEL", "MAC-", "UNIX")


def build_command(cmd: str, data: object = None) -> bytes:
    """
    Builds a byte array for ECI from the provided string and data

    Parameters
    ----------
    cmd: the command to send
    data: the data associated with the command; may be one of several types

    Returns
    -------
    The array of bytes that should be sent over the network

    Raises
    ------
    InvalidECICommand if the command is invalid

    See also
    --------
    InvalidECICommand and subclasses in eci.exceptions.py
    """
    # the byte array to send
    tx = None
    # placeholders for building NTP bytearr
    part_1 = sys_to_bytes(0, 2)
    part_2 = sys_to_bytes(0, 2)
    # begin validating
    if cmd not in byte_table:
        raise InvalidECICmd(cmd)
    tx = byte_table[cmd]
    if cmd not in requires_data:
        if data is not None:
            raise ECINoDataAllowed(cmd, data)
        else:
            return tx
    if data is None:
        raise ECIDataRequired(cmd)
    # iterate to validate individual command data requirements:
    if cmd == "Query":
        if data in allowed_endians:
            tx += data.encode("ASCII")
        else:
            raise ECIIllegalEndian(data)
    elif cmd == "ClockSync":
        if not isinstance(data, int):
            raise ECIClockNonInteger(data)
        else:
            tx += sys_to_bytes(data, 4)
    elif cmd == "NTPClockSync":
        try:
            tx += get_ntp_byte(data)
        except:
            raise ECINTPInvalid()
    elif cmd == "EventData":
        # TODO: make sure datagram is valid or construct helper
        if isinstance(data, bytes):
            tx += data
        else:
            raise ECIDataNotBytes(data)
    else:
        raise ECIUnknownException()
    return tx
