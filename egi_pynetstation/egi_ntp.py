#!/usr/bin/env python3
# -*- coding: utf-8 -*-

###############################################################################
# The MIT License (MIT)
#
# Copyright (C) 2009-2015 Charles-Francois Natali <cf.natali@gmail.com>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
###############################################################################

"""EGI's vendored fork of ntplib 0.4.0.

The packet implementation and public helper API are retained from ntplib so
the module remains easy to compare with upstream. The EGI changes are limited
to the local clock readings in :meth:`NTPClient.request`:

* Windows wall-clock timestamps use ``GetSystemTimePreciseAsFileTime`` instead
  of the pre-Python-3.13 ``time.time()`` implementation.
* Windows elapsed-time readings use ``time.perf_counter()`` instead of the
  pre-Python-3.13 ``time.monotonic()`` implementation.
* The receive-side wall and monotonic readings are attached to ``NTPStats`` so
  NetStation can place the NTP offset in the same clock frame as events.

On other platforms the clock functions are the same ones ntplib uses.
"""

import datetime
import functools
import socket
import struct
import sys
import time


class NTPException(Exception):
    """Exception raised by this module."""
    pass


class NTP:
    """Helper class defining constants."""

    _SYSTEM_EPOCH = datetime.date(*time.gmtime(0)[0:3])
    """system epoch"""
    _NTP_EPOCH = datetime.date(1900, 1, 1)
    """NTP epoch"""
    NTP_DELTA = (_SYSTEM_EPOCH - _NTP_EPOCH).days * 24 * 3600
    """delta between system and NTP time"""

    REF_ID_TABLE = {
        "GOES":  "Geostationary Orbit Environment Satellite",
        "GPS\0": "Global Position System",
        "GAL\0": "Galileo Positioning System",
        "PPS\0": "Generic pulse-per-second",
        "IRIG":  "Inter-Range Instrumentation Group",
        "WWVB":  "LF Radio WWVB Ft. Collins, CO 60 kHz",
        "DCF\0": "LF Radio DCF77 Mainflingen, DE 77.5 kHz",
        "HBG\0": "LF Radio HBG Prangins, HB 75 kHz",
        "MSF\0": "LF Radio MSF Anthorn, UK 60 kHz",
        "JJY\0": "LF Radio JJY Fukushima, JP 40 kHz, Saga, JP 60 kHz",
        "LORC":  "MF Radio LORAN C station, 100 kHz",
        "TDF\0": "MF Radio Allouis, FR 162 kHz",
        "CHU\0": "HF Radio CHU Ottawa, Ontario",
        "WWV\0": "HF Radio WWV Ft. Collins, CO",
        "WWVH":  "HF Radio WWVH Kauai, HI",
        "NIST":  "NIST telephone modem",
        "ACTS":  "NIST telephone modem",
        "USNO":  "USNO telephone modem",
        "PTB\0": "European telephone modem",
        "LOCL":  "uncalibrated local clock",
        "CESM":  "calibrated Cesium clock",
        "RBDM":  "calibrated Rubidium clock",
        "OMEG":  "OMEGA radionavigation system",
        "DCN\0": "DCN routing protocol",
        "TSP\0": "TSP time protocol",
        "DTS\0": "Digital Time Service",
        "ATOM":  "Atomic clock (calibrated)",
        "VLF\0": "VLF radio (OMEGA,, etc.)",
        "1PPS": "External 1 PPS input",
        "FREE": "(Internal clock)",
        "INIT": "(Initialization)",
        "ROA\0": "Real Observatorio de la Armada",
        "\0\0\0\0": "NULL",
    }
    """reference identifier table"""

    STRATUM_TABLE = {
        0: "unspecified or invalid",
        1: "primary reference (%s)",
    }
    """stratum table"""

    MODE_TABLE = {
        0: "reserved",
        1: "symmetric active",
        2: "symmetric passive",
        3: "client",
        4: "server",
        5: "broadcast",
        6: "reserved for NTP control messages",
        7: "reserved for private use",
    }
    """mode table"""

    LEAP_TABLE = {
        0: "no warning",
        1: "last minute of the day has 61 seconds",
        2: "last minute of the day has 59 seconds",
        3: "unknown (clock unsynchronized)",
    }
    """leap indicator table"""


NTP_DELTA = NTP.NTP_DELTA
"""Seconds between the NTP and system epochs."""

WINDOWS = sys.platform == 'win32'


def precise_system_time():
    """Return the wall clock used to timestamp local NTP packet I/O."""
    if not WINDOWS:
        return time.time()
    return _windows_precise_system_time()


def monotonic_time():
    """Return the monotonic clock used for EGI elapsed-time coordinates."""
    if WINDOWS:
        return time.perf_counter()
    return time.monotonic()


@functools.lru_cache(maxsize=1)
def _windows_precise_api():
    """Load and configure the precise Windows clock once per process."""
    try:
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [
                ('dwLowDateTime', wintypes.DWORD),
                ('dwHighDateTime', wintypes.DWORD),
            ]

        get_time = ctypes.WinDLL(
            'kernel32', use_last_error=True
        ).GetSystemTimePreciseAsFileTime
        get_time.argtypes = [ctypes.POINTER(FILETIME)]
        get_time.restype = None
        return ctypes, FILETIME, get_time
    except Exception as err:
        raise NTPException(
            'The precise Windows system clock is unavailable: %s' % err
        ) from err


def _windows_precise_system_time():
    """Read precise UTC system time on supported Windows versions."""
    try:
        ctypes, filetime_type, get_time = _windows_precise_api()
        filetime = filetime_type()
        get_time(ctypes.byref(filetime))
        ticks = (filetime.dwHighDateTime << 32) + filetime.dwLowDateTime
        # FILETIME is 100 ns ticks since 1601-01-01 UTC.
        return ticks / 10_000_000.0 - 11644473600.0
    except Exception as err:
        raise NTPException(
            'The precise Windows system clock is unavailable: %s' % err
        ) from err


class NTPPacket(object):
    """NTP packet class."""

    _PACKET_FORMAT = "!B B B b 11I"
    """packet format to pack/unpack"""

    def __init__(self, version=2, mode=3, tx_timestamp=0):
        """Create an NTP packet."""
        self.leap = 0
        self.version = version
        self.mode = mode
        self.stratum = 0
        self.poll = 0
        self.precision = 0
        self.root_delay = 0
        self.root_dispersion = 0
        self.ref_id = 0
        self.ref_timestamp = 0
        self.orig_timestamp = 0
        self.recv_timestamp = 0
        self.tx_timestamp = tx_timestamp

    def to_data(self):
        """Convert this NTPPacket to a buffer that can be sent."""
        try:
            packed = struct.pack(
                NTPPacket._PACKET_FORMAT,
                (self.leap << 6 | self.version << 3 | self.mode),
                self.stratum,
                self.poll,
                self.precision,
                _to_int(self.root_delay) << 16 | _to_frac(
                    self.root_delay, 16
                ),
                _to_int(self.root_dispersion) << 16 | _to_frac(
                    self.root_dispersion, 16
                ),
                self.ref_id,
                _to_int(self.ref_timestamp),
                _to_frac(self.ref_timestamp),
                _to_int(self.orig_timestamp),
                _to_frac(self.orig_timestamp),
                _to_int(self.recv_timestamp),
                _to_frac(self.recv_timestamp),
                _to_int(self.tx_timestamp),
                _to_frac(self.tx_timestamp),
            )
        except struct.error:
            raise NTPException("Invalid NTP packet fields.")
        return packed

    def from_data(self, data):
        """Populate this instance from an NTP packet payload."""
        try:
            unpacked = struct.unpack(
                NTPPacket._PACKET_FORMAT,
                data[0:struct.calcsize(NTPPacket._PACKET_FORMAT)],
            )
        except struct.error:
            raise NTPException("Invalid NTP packet.")

        self.leap = unpacked[0] >> 6 & 0x3
        self.version = unpacked[0] >> 3 & 0x7
        self.mode = unpacked[0] & 0x7
        self.stratum = unpacked[1]
        self.poll = unpacked[2]
        self.precision = unpacked[3]
        self.root_delay = float(unpacked[4]) / 2**16
        self.root_dispersion = float(unpacked[5]) / 2**16
        self.ref_id = unpacked[6]
        self.ref_timestamp = _to_time(unpacked[7], unpacked[8])
        self.orig_timestamp = _to_time(unpacked[9], unpacked[10])
        self.recv_timestamp = _to_time(unpacked[11], unpacked[12])
        self.tx_timestamp = _to_time(unpacked[13], unpacked[14])


class NTPStats(NTPPacket):
    """NTP reply statistics."""

    def __init__(self):
        """Create an NTP statistics object."""
        super(NTPStats, self).__init__()
        self.dest_timestamp = 0
        self.local_time = None
        self.monotonic_time = None
        self.python_monotonic_time = None

    @property
    def offset(self):
        """offset"""
        return ((self.recv_timestamp - self.orig_timestamp) +
                (self.tx_timestamp - self.dest_timestamp)) / 2

    @property
    def delay(self):
        """round-trip delay"""
        return ((self.dest_timestamp - self.orig_timestamp) -
                (self.tx_timestamp - self.recv_timestamp))

    @property
    def tx_time(self):
        """Transmit timestamp in system time."""
        return ntp_to_system_time(self.tx_timestamp)

    @property
    def recv_time(self):
        """Receive timestamp in system time."""
        return ntp_to_system_time(self.recv_timestamp)

    @property
    def orig_time(self):
        """Originate timestamp in system time."""
        return ntp_to_system_time(self.orig_timestamp)

    @property
    def ref_time(self):
        """Reference timestamp in system time."""
        return ntp_to_system_time(self.ref_timestamp)

    @property
    def dest_time(self):
        """Destination timestamp in system time."""
        return ntp_to_system_time(self.dest_timestamp)


class NTPClient(object):
    """NTP client session."""

    def __init__(self):
        """Constructor."""
        pass

    def request(self, host, version=2, port="ntp", timeout=5):
        """Query an NTP server and return an NTPStats object."""
        addrinfo = socket.getaddrinfo(host, port)[0]
        family, sockaddr = addrinfo[0], addrinfo[4]
        sock = socket.socket(family, socket.SOCK_DGRAM)

        try:
            sock.settimeout(timeout)

            tx_system = precise_system_time()
            query_packet = NTPPacket(
                mode=3,
                version=version,
                tx_timestamp=system_to_ntp_time(tx_system),
            )
            sock.sendto(query_packet.to_data(), sockaddr)

            src_addr = None,
            while src_addr[0] != sockaddr[0]:
                response_packet, src_addr = sock.recvfrom(256)

            dest_system = precise_system_time()
            dest_monotonic = monotonic_time()
            python_monotonic = time.monotonic()
            dest_timestamp = system_to_ntp_time(dest_system)
        except socket.timeout:
            raise NTPException("No response received from %s." % host)
        finally:
            sock.close()

        stats = NTPStats()
        stats.from_data(response_packet)
        stats.dest_timestamp = dest_timestamp
        stats.local_time = dest_system
        stats.monotonic_time = dest_monotonic
        stats.python_monotonic_time = python_monotonic
        return stats


def _to_int(timestamp):
    """Return the integral part of a timestamp."""
    return int(timestamp)


def _to_frac(timestamp, n=32):
    """Return the fractional part of a timestamp."""
    return int(abs(timestamp - _to_int(timestamp)) * 2**n)


def _to_time(integ, frac, n=32):
    """Return a timestamp from integral and fractional parts."""
    return integ + float(frac) / 2**n


def ntp_to_system_time(timestamp):
    """Convert an NTP timestamp to system time."""
    return timestamp - NTP.NTP_DELTA


def system_to_ntp_time(timestamp):
    """Convert a system timestamp to NTP time."""
    return timestamp + NTP.NTP_DELTA


def leap_to_text(leap):
    """Convert a leap indicator to text."""
    if leap in NTP.LEAP_TABLE:
        return NTP.LEAP_TABLE[leap]
    raise NTPException("Invalid leap indicator.")


def mode_to_text(mode):
    """Convert an NTP mode value to text."""
    if mode in NTP.MODE_TABLE:
        return NTP.MODE_TABLE[mode]
    raise NTPException("Invalid mode.")


def stratum_to_text(stratum):
    """Convert a stratum value to text."""
    if stratum in NTP.STRATUM_TABLE:
        return NTP.STRATUM_TABLE[stratum] % stratum
    if 1 < stratum < 16:
        return "secondary reference (%s)" % stratum
    if stratum == 16:
        return "unsynchronized (%s)" % stratum
    raise NTPException("Invalid stratum or reserved.")


def ref_id_to_text(ref_id, stratum=2):
    """Convert a reference clock identifier to text."""
    fields = (ref_id >> 24 & 0xff, ref_id >> 16 & 0xff,
              ref_id >> 8 & 0xff, ref_id & 0xff)

    if 0 <= stratum <= 1:
        text = "%c%c%c%c" % fields
        if text in NTP.REF_ID_TABLE:
            return NTP.REF_ID_TABLE[text]
        return "Unidentified reference source '%s'" % text
    if 2 <= stratum < 255:
        return "%d.%d.%d.%d" % fields
    raise NTPException("Invalid stratum.")
