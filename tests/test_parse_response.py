#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pytest
from eci.exceptions import *
from eci.eci import parse_response
from eci.util import sys_to_bytes, get_ntp_byte

# Exception Testing
def test_parse_failure():
    with pytest.raises(ECIFailure):
        test = parse_response(b'F')


def test_parse_no_recording():
    with pytest.raises(ECINoRecordingDeviceFailure):
        test = parse_response(b'R')


def test_parse_invalid_type():
    with pytest.raises(InvalidECIResponse) as e:
        test = parse_response('cat')
        assert e.message == 'Invalid ECI response type: str'


def test_parse_invalid_size():
    with pytest.raises(InvalidECIResponse) as e:
        test = parse_response(sys_to_bytes(0, 2))
        assert e.message == 'Invalid ECI response length: 2'


# Functionality Checks
def test_parse_gets_success():
    test = parse_response(b'Z')
    assert test == True


def test_parse_gets_version():
    test = parse_response(sys_to_bytes(2, 1))
    assert test == 2
    assert isinstance(test, int)


def test_parse_gets_NTP():
    test = parse_response(get_ntp_byte(1 + 2**-32))
    assert test == (1 + 2**-32)
