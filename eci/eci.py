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

class Command:
    """
    Object containing only a command and data

    Attributes
    ----------
    cmd : bytes
        The ECI command as a byte
    data : object
        The ECI command's data

    Static Attributes
    -----------------
    byte_table : map
        Mapping of ECI commands to the byte representation to send
    requires_data : tuple(str)
        Commands which require data
    allowed_endians : tuple(str)
        Allowed values of an endian
    """

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

    def __init__(self, cmd: str, data: object = None) -> None:
        # placeholders for building NTP bytearr
        part_1 = _sys_to_bytes(0, 2)
        part_2 = _sys_to_bytes(0, 2)
        # begin validating
        if cmd not in Command.byte_table:
            raise InvalidECICmd(cmd)
        self.cmd = Command.byte_table[cmd]
        if (cmd not in Command.requires_data):
            if data is not None:
                raise ECINoDataAllowed(cmd, data)
            else:
                self.data = None
                return
        if data is None:
            raise ECIDataRequired(cmd)
        # iterate to validate individual command data requirements:
        if cmd == 'Query':
            if not (data in Command.allowed_endians):
                raise ECIIllegalEndian(data)
            else:
                self.data = data.encode('ASCII')
        elif cmd == 'ClockSync':
            if not isinstance(data, int):
                raise ECIClockNonInteger(data)
            else:
                self.data = _sys_to_bytes(data, 4)
        elif cmd == 'NTPClockSync':
            if isinstance(data, int):
                # Convert number of seconds to 2-byte int with two 0-bytes
                part_1 = _sys_to_bytes(data, 4)
                part_2 = _sys_to_bytes(0, 4)
                self.data = part_1 + part_2
            elif isinstance(data, float):
                # Split number into two parts and build NTP bytestr
                part_2, part_1 = math.modf(data)
                print(part_1)
                part_1 = _sys_to_bytes(part_1, 4)
                part_2 = int(round(part_2/(2**-32)))
                part_2 = _sys_to_bytes(part_2, 4)
                self.data = part_1 + part_2
            elif isinstance(data, bytes):
                if len(data) != 8:
                    raise ECINTPInvalidByte(data)
                else:
                    self.data = data
            else:
                raise ECINTPInvalidType(data)
