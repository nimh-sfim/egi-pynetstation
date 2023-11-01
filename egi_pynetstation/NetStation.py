#!/us/bin/env python
# -*- coding: utf-8 -*-

"""Abstraction of the NetStation SDK as an object"""

import time
from math import floor
from typing import Union

from ntplib import system_to_ntp_time, NTPClient

from .eci import build_command, parse_response, allowed_endians, package_event
from .socket_wrapper import Socket
from .util import format_time
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
        Whether this instance is connected
    _endian: str
        The endianness of this machine
    _mstime: float
        Time in milliseconds last retrieved; NOT IMPLEMENTED CORRECTLY
    _ntp_ip: str
        The IP address of the NTP server on the amplifier

    Notes
    -----
    ADMONITION: currently non-NTP clock is not implemented; failing to
    supply an NTP IP address during connection will result in an error at
    runtime.

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
    """
    # TODO: implement simple clock using _mstime
    def __init__(self, ipv4: str, port: int, endian: str = 'NTEL') -> None:
        """Constructor for NetStation

        Parameters
        ----------
        ipv4: the ipv4 address to use for the amplifier
        port: the port number to use for the amplifier
        endian: the endianness of the machine; see eci.allowed_endians


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
        self._recording_start = None

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
                try:
                    func(*args, **kwargs)
                except ConnectionResetError:
                    raise RuntimeError(
                        "The server forcibly reset the connection, this "
                        "means you are likely trying to send too many "
                        "or excessively large events. "
                        "Consider modifying your experiment to send fewer."
                        "If the issue persists please contact the "
                        "developers with your full experiment and source "
                        "code here:\n"
                        "https://github.com/nimh-sfim/egi-pynetstation"
                    )
            else:
                raise NetStationUnconnected()
        return wrapper

    def connect(self, clock: str = 'ntp', ntp_ip: str = None) -> None:
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
        RuntimeError
            If you are a poor soul trying to use the simple clock
        """
        if clock not in ('ntp', 'simple'):
            raise NetStationIllegalArgument(clock)
        if clock == 'ntp' and ntp_ip is None:
            raise ValueError('NTP sync requires an NTP server IP')
        if clock == 'simple':
            raise RuntimeError(
                'You have requested the simple clock. '
                'We are perplexed by this choice when NTP is an option. '
                'Nonetheless, the real problem is that the author has not '
                'had time to implement simple clock as of this release. '
                'Stay tuned for more information, and sorry for the '
                'inconvenience.'
            )

        self._socket.connect()
        self._connected = True
        self._ntp_ip = ntp_ip
        self._command('Query', self._endian)
        self._command('Attention')

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
        cresponse = self._command('NTPClockSync', ntp_t)
        self._offset = response.offset
        self._syncepoch = t
        # TODO: Turn into a debug option
        # print('Sent local time: ' + format_time(t))
        # print(f'NTP offset is approx {self._offset}')
        # print(f'Syncepoch is approx {self._syncepoch}')

    @check_connected
    def resync(self):
        """Ensure clocks are synchronized"""
        self.ntpsync()

    @check_connected
    def resync_do_not_use_not_recommended(self):
        """Perform a re-synchronization: NOT RECOMMENDED; INCLUDED FOR COMPLETENESS"""
        if not self._ntp_ip:
            raise NetStationNoNTPIP()
        if not self._ntpsynced:
            self.ntpsync()
        c = NTPClient()
        response = c.request(self._ntp_ip, version=3)
        t = time.time()
        ntp_t = system_to_ntp_time(t)
        response = self._command('NTPReturnClock', ntp_t + response.offset)
        self.send_event(event_type="RESY")
        # TODO: Turn into a debug option
        # print('Sent local time: ' + format_time(t))
        # print(f'NTP offset is approx {self._offset}')
        # print(f'Response is {response} (or {format_time(response)}')

    @check_connected
    def disconnect(self) -> None:
        """Close the TCP/IP connection."""
        self._command('Exit')
        self._socket.disconnect()
        self._connected = False

    @check_connected
    def begin_rec(self) -> None:
        """Begin Recording; also performs NTP sync"""
        if self._ntp_ip:
            self.ntpsync()
        # TODO: verify simple clock works correctly
        elif clock == 'simple':
            t = floor(time.time() * 1000)
            self._command('ClockSync', t)
            self._syncepoch = t

        self._recording_start = time.time()
        self._command('BeginRecording')

    @check_connected
    def end_rec(self) -> None:
        """End Recording"""
        self._command('EndRecording')
        self._recording_start = None

    @check_connected
    def send_event(
        self,
        start='now',
        duration: float = 0.001,
        event_type: str = ' ' * 4,
        label: str = ' ' * 4,
        desc: str = ' ' * 4,
        data: dict = {},
    ) -> None:
        """Send event to amplifier

        Parameters
        ----------
        start: str, float, int
            The start time for the event; if string, use "now" only.
            Otherwise state the amount of time since recording in seconds.
            Default "now".
        duration: float
            The duration of the event in seconds; default 0.001
        event_type: str
            The event type to use; must be 4 characters exactly. Default "     "
        label: str
            The label to use; must be <= 256 characters . Default "    "
        desc: str
            The description to use; must be <= 256 characters. Default "    "
        data: dict
            The event data to send; see Notes for more information.

        Notes
        -----
        When using the event sender, "now" is typically very precise.
        Tests on a Windows 7 machine with PsychoPy indicate that the
        latency in real time is about 54 +/- 3 ms for a short experiment.
        More data to come; stay tuned.

        It is not necessary to send any data; in fact, this is recommended
        as it takes some (admittedly small) amount of time to package the
        data.
        It is recommended to very clearly document what each event marker
        means and use "event_type" as the main identifier by convention.

        The data to send has several restrictions, enumerated below:
        - The key values must be precisely 4 ASCII characters in length.
        - Data must be one of several types:
          - boolean
          - floating-point (will be double-precision)
          - integer (will be "long" precision)
          - string (ASCII characters only)
        - The dictionary representing the data must be shallow; no nested
          dictionaries.

        See Also
        --------
        eci.eci for explanations of the internals of the packaging
        """
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

    def rec_start(self) -> float:
        """Get recording start time from time.time()

        Returns
        -------
        Floating-point time of recording start
        """
        return self._recording_start

    def since_start(self) -> float:
        """DO NOT USE; Get difference in time since recording start

        Returns
        -------
        The number of seconds since recording start
        """
        if self._recording_start is not None:
            return time.time() - self._recording_start
        else:
            return None

    def _command(self, cmd: str, data=None) -> Union[bool, float, int]:
        """Send a command to the amplifier; please do not use as this is
        internal.

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
        # TODO: turn into a debug option
        # print(f'{cyan}Sending command: {eci_cmd}{reset}')
        self._socket.write(eci_cmd)
        return parse_response(self._socket.read())
