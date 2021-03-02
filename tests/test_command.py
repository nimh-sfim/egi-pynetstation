#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import struct
import pytest
from eci.exceptions import *
from eci.eci import Command, _sys_to_bytes

# Exception Testing
def test_cmd_raises_bad_command():
    with pytest.raises(InvalidECICmd):
        test = Command('Eixt')


def test_cmd_raises_for_illegal_data():
    with pytest.raises(ECINoDataAllowed):
        test = Command('Exit', 0)


def test_cmd_raises_for_required_data():
    with pytest.raises(ECIDataRequired):
        test = Command('Query')

def test_cmd_raises_for_illegal_endian():
    with pytest.raises(ECIIllegalEndian):
        test = Command('Query', 1)


def test_cmd_raises_for_non_integer_clock():
    with pytest.raises(ECIClockNonInteger):
        test = Command('ClockSync', 0.15)

    with pytest.raises(ECIClockNonInteger):
        test = Command('ClockSync', 'cat')


def test_cmd_ntp_raises_for_invalid_byte():
    with pytest.raises(ECINTPInvalidByte):
        test = Command('NTPClockSync', _sys_to_bytes(2, 5))


def test_cmd_ntp_raises_for_invalid_type():
    with pytest.raises(ECINTPInvalidType):
        test = Command('NTPClockSync', 'cat')


# Correct functioning testing
def test_cmd_no_data():
    test = Command('Exit')
    assert test.cmd == b'X'
    assert test.data == None


def test_cmd_formats_endian():
    test = Command('Query', 'MAC-')
    assert test.cmd == b'Q'
    assert test.data == b'MAC-'


def test_cmd_ntp_formats_int():
    test = Command('NTPClockSync', 1)
    part_1 = test.data[0:4]
    part_2 = test.data[4:]
    val_1 = struct.unpack("<L", part_1)[0]
    val_2 = struct.unpack("<L", part_2)[0]
    assert val_1 == 1
    assert val_2 == 0


def test_cmd_ntp_formats_float():
    test = Command('NTPClockSync', 1 + 2**-32)
    part_1 = test.data[0:4]
    part_2 = test.data[4:]
    val_1 = struct.unpack("<L", part_1)[0]
    val_2 = struct.unpack("<L", part_2)[0]
    assert val_1 == 1
    assert val_2 == 1
