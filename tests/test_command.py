#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import struct
import pytest
from eci.exceptions import *
from eci.eci import build_command
from eci.util import sys_to_bytes

# Exception Testing
def test_cmd_raises_bad_command():
    with pytest.raises(InvalidECICmd):
        test = build_command('Eixt')


def test_cmd_raises_for_illegal_data():
    with pytest.raises(ECINoDataAllowed):
        test = build_command('Exit', 0)


def test_cmd_raises_for_required_data():
    with pytest.raises(ECIDataRequired):
        test = build_command('Query')

def test_cmd_raises_for_illegal_endian():
    with pytest.raises(ECIIllegalEndian):
        test = build_command('Query', 1)


def test_cmd_raises_for_non_integer_clock():
    with pytest.raises(ECIClockNonInteger):
        test = build_command('ClockSync', 0.15)

    with pytest.raises(ECIClockNonInteger):
        test = build_command('ClockSync', 'cat')


def test_cmd_ntp_raises_for_invalid_byte():
    with pytest.raises(ECINTPInvalidByte):
        test = build_command('NTPClockSync', sys_to_bytes(2, 5))


def test_cmd_ntp_raises_for_invalid_type():
    with pytest.raises(ECINTPInvalidType):
        test = build_command('NTPClockSync', 'cat')


def test_cmd_raises_for_nonbyte_data():
    with pytest.raises(ECIDataNotBytes):
        test = build_command('EventData', 'cat')


# Correct functioning testing
def test_cmd_no_data():
    test = build_command('Exit')
    assert test.decode('ascii') == 'X'


def test_cmd_formats_endian():
    test = build_command('Query', 'MAC-')
    assert test.decode('ascii') == 'QMAC-'


def test_cmd_ntp_formats_int():
    test = build_command('NTPClockSync', 1)
    part_1 = test[1:5]
    part_2 = test[5:]
    val_1 = struct.unpack("<L", part_1)[0]
    val_2 = struct.unpack("<L", part_2)[0]
    assert val_1 == 1
    assert val_2 == 0


def test_cmd_ntp_formats_float():
    test = build_command('NTPClockSync', 1 + 2**-32)
    part_1 = test[1:5]
    part_2 = test[5:]
    val_1 = struct.unpack("<L", part_1)[0]
    val_2 = struct.unpack("<L", part_2)[0]
    assert val_1 == 1
    assert val_2 == 1
