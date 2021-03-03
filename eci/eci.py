#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""ECI controls and returns"""

import sys
import math

from .exceptions import *

def _sys_to_bytes(number: int, size: int) -> bytes:
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

byte_table = {
    'Query': b'Q',
    'NewQuery': b'Y',
    'Exit': b'X',
    'BeginRecording': b'B',
    'EndRecording': b'E',
    'Attention': b'A',
    'ClockSync': b'T',
    'NTPClockSync': b'N',
    'NTPReturnClock': b'S',
    'EventData': b'D',
}

requires_data = ('Query', 'ClockSync', 'NTPClockSync', 'EventData')

allowed_endians = ('NTEL', 'MAC-', 'UNIX')

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
    """
    # the byte array to send
    tx = None
    # placeholders for building NTP bytearr
    part_1 = _sys_to_bytes(0, 2)
    part_2 = _sys_to_bytes(0, 2)
    # begin validating
    if cmd not in byte_table:
        raise InvalidECICmd(cmd)
    tx = byte_table[cmd]
    if (cmd not in requires_data):
        if data is not None:
           raise ECINoDataAllowed(cmd, data)
        else:
           return tx
    if data is None:
        raise ECIDataRequired(cmd)
    # iterate to validate individual command data requirements:
    if cmd == 'Query':
        if data in allowed_endians:
           tx += data.encode('ASCII')
        else:
           raise ECIIllegalEndian(data)
    elif cmd == 'ClockSync':
        if not isinstance(data, int):
           raise ECIClockNonInteger(data)
        else:
           tx += _sys_to_bytes(data, 4)
    elif cmd == 'NTPClockSync':
        if isinstance(data, int):
           # Convert number of seconds to 2-byte int with two 0-bytes
           part_1 = _sys_to_bytes(data, 4)
           part_2 = _sys_to_bytes(0, 4)
           tx += part_1 + part_2
        elif isinstance(data, float):
           # Split number into two parts and build NTP bytestr
           part_2, part_1 = math.modf(data)
           print(part_1)
           part_1 = _sys_to_bytes(part_1, 4)
           part_2 = int(round(part_2/(2**-32)))
           part_2 = _sys_to_bytes(part_2, 4)
           tx += part_1 + part_2
        elif isinstance(data, bytes):
           if len(data) == 8:
                tx += data
           else:
                raise ECINTPInvalidByte(data)
        else:
           raise ECINTPInvalidType(data)
    elif cmd == 'EventData':
    # TODO: make sure datagram is valid or construct helper
        if isinstance(data, bytes):
           tx += data
        else:
           raise ECIDataNotBytes(data)
    else:
        raise ECIUnknownException()
    return tx
