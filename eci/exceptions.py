#!/usr/bin/env python3
# -*- coding: utf-8 -*-

class SocketException(Exception):
    """Base class for Socket exceptions"""
    pass


class SocketIncompleteTransmission(Exception):
    """Exception for incomplete transmission on write to address"""
    def __init__(self, transmitted: int, expected: int):
        self.message = (
            '%d bytes of %d were transmitted' % (transmitted, expected)
        )


class ECIException(Exception):
    """Base class for ECI exceptions"""
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


# Netstation Errors
class NetStationError(ECIException):
    """Base class for NetStation exceptions"""
    pass


class NetStationUnconnected(NetStationError):
    """Exception raised for attempting communication before connecting"""
    def __init__(self) -> None:
        self.message = 'Attempted operation before connecting to amp'


class NetStationIllegalArgument(NetStationError):
    """Exception for passing an illegal argument"""
    def __init__(self, arg: object) -> None:
        self.message = '%s is an illegal argument' % arg


class NetStationNoNTPIP(NetStationError):
    """Exception for if you attempt to perform NTP sync with no IP"""
    def __init__(self) -> None:
        self.message = (
            'Attempted to perform NTP sync without supplying NTP IP.'
            'Please review the documentation for NetStation and revise '
            'your experiment.'
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
        self.message = f'Command {cmd} does not take data: {data}'


class ECIDataRequired(InvalidECICommand):
    """Exception for not passing data when required"""
    def __init__(self, cmd: str) -> None:
        self.message = f'Command {cmd} requires an argument'


class ECIIllegalEndian(InvalidECICommand):
    """Exception for passing illegal endian type"""
    def __init__(self, endian: str) -> None:
        self.message = f'{endian} is not a valid endian'


class ECIClockNonInteger(InvalidECICommand):
    """Exception for passing non-integer for clock synchronization"""
    def __init__(self, noninteger: object) -> None:
        self.message = f'{noninteger} is not a valid integer'


class ECINTPInvalid(InvalidECICommand):
    """Exception for failure to create NTPv4 time from given data"""
    pass


class ECIDataNotBytes(InvalidECICommand):
    """Exception for non-bytes type for sending data"""
    def __init__(self, o: object) -> None:
        t = type(o)
        self.message = f'Event Data requires type bytes, is type {t}'


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
        # TODO: add more specificity with sub-exceptions
        if isinstance(o, bytes):
            self.message = f'Invalid ECI response length: {o}'
        else:
            self.message = f'Invalid ECI response type: {type(o)}'


# NTP exceptions
class NTPException(ECIException):
    """Exception to derive from for NTP exceptions"""
    pass


class NTPInvalidByte(NTPException):
    """Exception for passing an invalid NTP byte array"""
    def __init__(self, bytearr: bytes) -> None:
        self.message = f'{len(bytearr)} bytes given instead of 8'


class NTPInvalidType(NTPException):
    """Exception for invalid type for NTP time formatting"""
    def __init__(self, o: object) -> None:
        self.message = f'Type {type(o)} is not valid for NTP sync'
