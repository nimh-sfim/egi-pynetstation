#!/usr/bin/env python3
# -*- coding: utf-8 -*-

class ECIException(Exception):
    pass

class InvalidECICommand(ECIException):
    """Exception raised for trying to send an invalid ECI command"""
    pass


class InvalidECICmd(InvalidECICommand):
    """Exception for an invalid command"""
    def __init__(self, invalidcmd: str) -> None:
        self.message = 'Invalid ECI command: ' + invalidcmd


class ECINoDataAllowed(InvalidECICommand):
    """Exception for passing data when not allowed"""
    def __init__(self, cmd: str, data: object) -> None:
        self.message = 'Command %s does not take data: %s' % (cmd, data)


class ECIDataRequired(InvalidECICommand):
    """Exception for not passing data when required"""
    def __init__(self, cmd: str) -> None:
        self.message = 'Command %s requires an argument' % cmd


class ECIIllegalEndian(InvalidECICommand):
    """Exception for passing illegal endian type"""
    def __init__(self, endian: str) -> None:
        self.message = '%s is not a valid endian' % endian


class ECIClockNonInteger(InvalidECICommand):
    """Exception for passing non-integer for clock synchronization"""
    def __init__(self, noninteger: object) -> None:
        self.message = '%s is not a valid integer' % noninteger


class ECINTPException(InvalidECICommand):
    """Exception to derive from for NTP exceptions"""
    pass


class ECINTPNonNumber(ECINTPException):
    """Exception for passing non-number values for NTP synchronization"""
    def __init__(self, nonnumber: object) -> None:
        self.message = '%s is not a number' % nonnumber


class ECINTPInvalidByte(ECINTPException):
    """Exception for passing an invalid NTP byte array"""
    def __init__(self, bytearr: bytes) -> None:
        self.message = '%d bytes given instead of 4' % len(bytearr)


class ECINTPInvalidType(ECINTPException):
    """Exception for invalid type for sending NTP sync command"""
    def __init__(self, o: object) -> None:
        self.message = 'Type %s is not valid for NTP sync' % type(o)
