#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import struct
import pytest
from eci.exceptions import *
from eci.util import sys_to_bytes, get_ntp_float, ntp_res

# Exception Testing
def test_raises_for_invalid_NTP():
    with pytest.raises(NTPInvalidByte):
        test = get_ntp_float(sys_to_bytes(2, 5))


def test_raises_for_nonbyte_data():
    with pytest.raises(NTPInvalidType):
        test = get_ntp_float('cat')


# Correct functioning testing
def test_works():
    bytearr = sys_to_bytes(0, 8)
    test = get_ntp_float(bytearr)
    assert test == 0.0

    bytearr = sys_to_bytes(5, 4) + sys_to_bytes(3, 4)
    test = get_ntp_float(bytearr)
    assert test == (5 + 3*ntp_res)
