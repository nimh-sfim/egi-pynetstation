#!/usr/bin/env python
# -*- coding: utf-8 -*-

from time import time, mktime, localtime
from math import floor
from typing import Union

from ntplib import system_to_ntp_time, ntp_to_system_time

from .eci import build_command, parse_response, allowed_endians, package_event
from .util import format_time
from .socket_wrapper import Socket
from .exceptions import *

# Define the NTP epoch

class NetStation(object):
    """Netstation object to interact with the amplifier.

    Attributes
    ----------
    _socket : Socket
        The socket to use to control the amplifier
    _connected: bool
        Whether this NetStation is connected
    _endian: str
        The endianness of this machine
    _mstime: float
        Time in milliseconds last retrieved
    """
    # TODO: implement proper clock sync, update _mstime
    def __init__(self, ipv4: str, port: int, endian: str = 'NTEL') -> None:
        """Constructor for NetStation

        Parameters
        ----------
        ipv4: the ipv4 address to use for the amplifier
        port: the port number to use for the amplifier
        endian: the endianness of the machine; see eci.allowed_endians

        Notes
        -----
        Some behavior is not properly documented in the SDK guide. You may
        need to refer to docstrings, code comments, and the README in order
        to get the complete specification for NetStation behavior rather
        than the SDK guide. Notable deviations:
        - eci_NTPReturnClock *requires* an NTPv4 timecode, even though in
          the documentation "Experimental Control Interface (ECI) Commands
          and Return Values" table, "Follows controller command if more
          data expected" column is blank (page 8).
        - eci_NTPReturnClock returns a byte representing 'S', followed by
          the 8-byte NTPv4 timecode, rather than the NTPv4 timecode as
          described in the above table's "ECI Return Values" sub-table.
          This means that the total size of the server response is actually
          9 bytes rather than 8, and that the response's first byte is 'S'
          rather than 'Z'.
        - eci_Identify actually responses with 'I' plus the byte
          representation of the identity, for a total of two bytes rather
          than one
        The default endianness is determined based on the use of a 2020
        MacBook Pro 13" with i5, on MacOS 10.15.7. Feel free to inform the
        authors of the appropriate endianness for other platforms so that
        we can add that to the documentation!

        See Also
        --------
        eci.eci: module for parsing eci commands/responses
        """
        self._socket = Socket(ipv4, port)
        self._connected = False
        if not (endian in allowed_endians):
            raise NetStationIllegalArgument(endian)
        self._endian = endian
        self._mstime = None

    def check_connected(func) -> None:
        """Decorator to raise exception if not connected

        Parameters
        ----------
        func: a function which has no parameters

        Raises
        ------
        NetStationUnconnected
            If NetStation hasn't had .connect() run yet
        """
        def wrapper(*args, **kwargs):
            if args[0]._connected:
                func(*args, **kwargs)
            else:
                raise NetStationUnconnected()
        return wrapper

    def connect(self, clock: str = 'ntp') -> None:
        """Connect to the Netstation machine via TCP/IP

        Parameters
        ----------
        clock: either 'ntp' or 'simple', indicating clock sync method

        Raises
        ------
        NetStationIllegalArgument
            If clock is not 'ntp' or 'simple'
        ConnectionRefusedError
            If the server is not listening
        """
        if clock not in ('ntp', 'simple'):
            raise NetStationIllegalArgument(clock)
        self._socket.connect()
        self._connected = True
        self._command('Query', self._endian)
        self._command('Attention')
        if clock == 'ntp':
            # TODO: implement NTP correctly
            t = time()
            print('Sent local time:  ' + format_time(t))
            t = system_to_ntp_time(t)
            self._command('NTPClockSync', t)
        elif clock == 'simple':
            t = time()
            self._command('ClockSync', t)

    @check_connected
    def disconnect(self) -> None:
        """Close the TCP/IP connection."""
        self._socket.disconnect()
        self._connected = False

    @check_connected
    def begin_rec(self) -> None:
        """Begin Recording"""
        self._command('BeginRecording')

    @check_connected
    def end_rec(self) -> None:
        """End Recording"""
        self._command('EndRecording')

    @check_connected
    def resync(self) -> float:
        """Re-synchronize with the amplifier

        Returns
        -------
        The current NTP epoch time
        """
        t = time()
        data = system_to_ntp_time(t)
        self._mstime = self._command('NTPReturnClock', data)
        # TODO: remove this debug info
        print('Sent local time: ' + format_time(t))
        amptime = ntp_to_system_time(self._mstime)
        print('Received amp time: ' + format_time(amptime))

    @check_connected
    def send_event(
        self,
        start: float,
        duration: float = 0.001,
        event_type: str = ' '*4,
        label: str = ' '*4,
        desc: str = ' '*4,
        data: dict = {},
    ) -> None:
        """Send event to amplifier

        Parameters
        ----------
        data: the event data to send

        Notes
        -----
        Current does not check that event data is valid!
        """
        # TODO: make sure data sent is valid; implement in eci.eci and
        # reference here
        data = package_event(
            start, duration, event_type, label, desc, data
        )
        self._command('EventData', data)

    def _command(self, cmd: str, data=None) -> Union[bool, float, int]:
        """Send a command to the amplifier

        Parameters
        ----------
        cmd: the command to send
        data: the data to send with it

        Returns
        -------
        The server response

        Raises
        ------
        SocketIncompleteTransmission if transmission cannot complete
        InvalidECICommand if the command is invalid

        See Also
        --------
        eci.eci: module for building commands and parsing responses
        """
        if not self._connected:
            raise NetStationUnconnected()
        eci_cmd = build_command(cmd, data)
        self._socket.write(eci_cmd)
        return parse_response(self._socket.read())

    def _last_sync(self) -> float:
        """Get last sync time in NTP epoch

        Returns
        -------
        The amplifer time of last sync in system time
        """

        return ntp_to_system_time(self._mstime)
