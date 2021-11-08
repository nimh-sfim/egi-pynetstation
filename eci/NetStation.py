#!/us/bin/env python
# -*- coding: utf-8 -*-

import time
from time import ctime, sleep
from math import floor
from typing import Union

from ntplib import system_to_ntp_time, ntp_to_system_time, NTPClient

from .eci import build_command, parse_response, allowed_endians, package_event
from .util import format_time, ntp_epoch, unix_epoch
from .socket_wrapper import Socket
from .exceptions import *

cyan = '\u001b[36;1m'
reset = '\u001b[0m'


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

    def connect(self, clock: str = 'ntp', ntp_ip = None) -> None:
        """Connect to the Netstation machine via TCP/IP

        Parameters
        ----------
        clock: either 'ntp' or 'simple', indicating clock sync method
        ntp_ip: the IP address of the NTP server on the amplifier

        Raises
        ------
        NetStationIllegalArgument
            If clock is not 'ntp' or 'simple'
        ConnectionRefusedError
            If the server is not listening
        """
        if clock not in ('ntp', 'simple'):
            raise NetStationIllegalArgument(clock)
        if clock == 'ntp' and ntp_ip is None:
            raise ValueError('NTP sync requires an NTP server IP')

        self._socket.connect()
        self._connected = True
        self._ntp_ip = ntp_ip
        self._command('Query', self._endian)
        self._command('Attention')

        if clock == 'ntp':
            self.ntpsync()
        elif clock == 'simple':
            t = floor(time.time() * 1000)
            self._command('ClockSync', t)
            self._syncepoch = t

    @check_connected
    def ntpsync(self):
        """Perform an NTP synchronization"""
        self._ntpsynced = True
        self._command('Attention')
        if not self._ntp_ip:
            raise NetStationNoNTPIP()
        c = NTPClient()
        response = c.request(self._ntp_ip, version=3)
        t = time.time()
        ntp_t = system_to_ntp_time(t + response.offset)
        tt = self._command('NTPClockSync', ntp_t)
        self._offset = response.offset
        self._syncepoch = t
        print('Sent local time: ' + format_time(t))
        print(f'NTP offset is approx {self._offset}')
        print(f'Syncepoch is approx {self._syncepoch}')

    @check_connected
    def resync(self):
        """Perform a re-synchronization"""
        if not self._ntp_ip:
            raise NetStationNoNTPIP()
        if not self._ntpsynced:
            self.ntpsync()
        c = NTPClient()
        response = c.request(self._ntp_ip, version=3)
        t = time.time()
        ntp_t = system_to_ntp_time(t)
        tt = self._command('NTPReturnClock', ntp_t + response.offset)
        self._offset = response.offset
        print('Sent local time: ' + format_time(t))
        print(f'NTP offset is approx {self._offset}')


    @check_connected
    def disconnect(self) -> None:
        """Close the TCP/IP connection."""
        self._command('Exit')
        self._socket.disconnect()
        self._connected = False

    @check_connected
    def begin_rec(self) -> None:
        """Begin Recording"""
        self._recording_start = time.time()
        self._command('BeginRecording')

    @check_connected
    def end_rec(self) -> None:
        """End Recording"""
        self._command('EndRecording')

    @check_connected
    def send_event(
        self,
        start = 'now',
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
        if start == 'now':
            start = time.time() - self._syncepoch
        elif isinstance(start, float):
            start = start
        else:
            t_start = type(start)
            return TypeError(
                f'Start is type {t_start}, should be str "now" or float'
            )
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
        print(f'{cyan}Sending command: {eci_cmd}{reset}')
        self._socket.write(eci_cmd)
        return parse_response(self._socket.read())

    def rec_start(self) -> float:
        """Get recording start time from time.time()

        Returns
        -------
        Floating-point time of recording start
        """
        return self._recording_start

    def since_start(self) -> float:
        """Get difference in time since recording start

        Returns
        -------
        The number of seconds since recording start
        """
        return time.time() - self._recording_start

    def _last_sync(self) -> float:
        """Get last sync time in NTP epoch

        Returns
        -------
        The amplifer time of last sync in system time
        """

        return ntp_to_system_time(self._mstime)
