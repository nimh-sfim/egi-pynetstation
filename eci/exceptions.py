#!/usr/bin/env python3
# -*- coding: utf-8 -*-

class ECIException(Exception):
    pass

class ECIUnknownException(ECIException):
    """Exception raised for an unknown problem"""
    def __init__(self) -> None:
        self.message = (
            'An unknown exception has occurred in the ECI module.'
            'This is likely due to programmer error.'
            'Please post an issue at the following location:'
            'https://github.com/nimh-sfim/PsychoPy3_EGI_NTP'
        )


# Invalid ECI commands
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


class ECINTPInvalid(InvalidECICommand):
    """Exception for failure to create NTPv4 time from given data"""
    pass


class ECIDataNotBytes(InvalidECICommand):
    """Exception for non-bytes type for sending data"""
    def __init__(self, o: object) -> None:
        t = type(o)
        self.message = 'Event Data requires type bytes, is type %s' % t


# Amp Failure exceptions
class ECIResponseFailure(ECIException):
    """Exception to derive from for amp failures"""
    pass


class ECIFailure(ECIResponseFailure):
    """Exception for when the amp responds with simple fail"""
    def __init__(self) -> None:
        self.message = 'Amp responded with Failure'


class ECINoRecordingDeviceFailure(ECIResponseFailure):
    """Exception for when the amp responds witth no recording device"""
    def __init__(self):
        self.message = 'No recording device found; please check setup'


class InvalidECIResponse(ECIResponseFailure):
    """Exception for when an invalid amp response is passed"""
    def __init__(self, o: object) -> None:
        if isinstance(o, bytes):
            self.message = (
                'Invalid ECI response length: %d of 1' % len(o)
            )
        else:
            self.message = 'Invalid ECI response type: %s' % type(o)


# NTP exceptions
class NTPException(ECIException):
    """Exception to derive from for NTP exceptions"""
    pass


class NTPInvalidByte(NTPException):
    """Exception for passing an invalid NTP byte array"""
    def __init__(self, bytearr: bytes) -> None:
        self.message = '%d bytes given instead of 8' % len(bytearr)


class NTPInvalidType(NTPException):
    """Exception for invalid type for NTP time formatting"""
    def __init__(self, o: object) -> None:
        self.message = 'Type %s is not valid for NTP sync' % type(o)
