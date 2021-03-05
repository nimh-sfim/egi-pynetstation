#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""ECI controls and returns"""

import sys
import math
from typing import Union

from .exceptions import *
from .util import sys_to_bytes, sys_from_bytes, get_ntp_byte, get_ntp_float


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

requires_data = ("Query", "ClockSync", "NTPClockSync", "EventData",
                    'NTPReturnClock')
# NOTE: NTPReturnClock does not indicate a need to send an NTPv4 in the
# SDK documentation; however, testing indicates that it is required

allowed_endians = ("NTEL", "MAC-", "UNIX")

# Python converts the bytes to ints when indexing; this is more legible
INT_VAL_I = 73
INT_VAL_S = 83


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
    elif cmd == "NTPClockSync" or cmd == 'NTPReturnClock':
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


def parse_response(bytearr: bytes) -> Union[bool, float, int]:
    """Parses ECI response

    Parameters
    ----------
    bytearr: the byte array to parse (should be size 1)

    Returns
    -------
    Either True or the value of the ECI Identity

    Raises
    ------
    ECIResponseFailure for all failures
    ECIFailure if the amp responds with failure
    ECINoRecordingDeviceFailure if the failure is a result of no recording
    TypeError if the object passed isn't type bytes
    """
    arrlength = 0
    if isinstance(bytearr, bytes):
        arrlength = len(bytearr)
        if arrlength == 1:
            if bytearr == b'Z':
                return True
            if bytearr == b'F':
                raise ECIFailure()
            if bytearr == b'R':
                raise ECINoRecordingDeviceFailure()
            else:
                raise InvalidECIResponse(bytearr)
        elif arrlength == 2:
            # Identify version number
            # NOTE: this deviates from the SDK documentation, which
            # indicates a 1-byte response
            if bytearr[0] == INT_VAL_I:
                return sys_from_bytes(bytearr[1:])
            else:
                raise InvalidECIResponse(bytearr)
        elif arrlength == 8:
            # We've been given an NTPv4-formatted bytearr
            return get_ntp_float(bytearr)
        elif arrlength == 9:
            # We've been given an 'S' plus NTPv4-formatted bytearr
            # NOTE: this return of size 9 bytes rather than 8 is not
            # properly documented in the SDK guide
            if bytearr[0] == INT_VAL_S:
                return get_ntp_float(bytearr[1:])
            else:
                raise InvalidECIResponse(bytearr)
        else:
            raise InvalidECIResponse(bytearr)
    else:
        raise InvalidECIResponse(bytearr)
