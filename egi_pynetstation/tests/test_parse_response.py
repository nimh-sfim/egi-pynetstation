#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pytest
from egi_pynetstation.exceptions import *
from egi_pynetstation.eci import parse_response, INT_VAL_S
from egi_pynetstation.util import sys_to_bytes, get_ntp_byte

invalid_id = sys_to_bytes(0, 1)
valid_number = sys_to_bytes(56, 1)
correct_ntp = 1 + 2**-32
valid_ntp = get_ntp_byte(correct_ntp)


# Exception Testing
def test_parse_failure():
    with pytest.raises(ECIFailure):
        _ = parse_response(b'F')


def test_parse_no_recording():
    with pytest.raises(ECINoRecordingDeviceFailure):
        _ = parse_response(b'R')


def test_parse_singleton():
    with pytest.raises(InvalidECIResponse):
        _ = parse_response(b'D')


def test_parse_invalid_type():
    with pytest.raises(InvalidECIResponse):
        _ = parse_response('cat')


def test_parse_illegal_ident():
    with pytest.raises(InvalidECIResponse):
        _ = parse_response(invalid_id + valid_number)


def test_parse_illegal_ident_ntp():
    with pytest.raises(InvalidECIResponse):
        _ = parse_response(invalid_id + valid_ntp)


def test_parse_invalid_size():
    with pytest.raises(InvalidECIResponse):
        _ = parse_response(sys_to_bytes(0, 5))


# Functionality Checks
def test_parse_gets_success():
    test = parse_response(b'Z')
    assert test is True


def test_parse_gets_version():
    test = parse_response(b'I' + sys_to_bytes(2, 1))
    assert test == 2
    assert isinstance(test, int)


def test_parse_gets_NTP():
    id_byte = sys_to_bytes(INT_VAL_S, 1)

    test = parse_response(valid_ntp)
    assert test == correct_ntp

    test = parse_response(id_byte + valid_ntp)
    assert test == correct_ntp
