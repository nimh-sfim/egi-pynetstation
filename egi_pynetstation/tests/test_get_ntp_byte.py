#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import struct
import pytest
from egi_pynetstation.exceptions import *
from egi_pynetstation.util import sys_to_bytes, get_ntp_byte


# Exception Testing
def test_raises_for_invalid_NTP():
    with pytest.raises(NTPInvalidByte):
        _ = get_ntp_byte(sys_to_bytes(2, 5))


def test_raises_for_nonbyte_data():
    with pytest.raises(NTPInvalidType):
        _ = get_ntp_byte('cat')


# Correct functioning testing
def test_formats_int():
    test = get_ntp_byte(1)
    part_1 = test[0:4]
    part_2 = test[4:]
    val_1 = struct.unpack("<L", part_1)[0]
    val_2 = struct.unpack("<L", part_2)[0]
    assert val_1 == 1
    assert val_2 == 0


def test_formats_float():
    test = get_ntp_byte(1 + 2**-32)
    part_1 = test[0:4]
    part_2 = test[4:]
    val_1 = struct.unpack("<L", part_1)[0]
    val_2 = struct.unpack("<L", part_2)[0]
    assert val_1 == 1
    assert val_2 == 1
