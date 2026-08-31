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
to the clock handling and validation in :meth:`NTPClient.request`:

* Windows wall-clock timestamps use ``GetSystemTimePreciseAsFileTime`` instead
  of the pre-Python-3.13 ``time.time()`` implementation.
* Elapsed-time readings use ``time.perf_counter()`` on every platform
  instead of ``time.monotonic()``, whose pre-Python-3.13 Windows
  implementation ticks at 15.6 ms.
* The receive-side wall and monotonic readings are attached to ``NTPStats`` so
  NetStation can place the NTP offset in the same clock frame as events.
* Replies with an invalid mode, clock state, stratum, or originate timestamp
  are rejected before they can anchor a recording.

On macOS and Linux ``perf_counter()`` and ``monotonic()`` share an
implementation, so the only behavioural change off Windows is that the
package now exercises one clock path everywhere.
"""

import datetime
import functools
import socket
import struct
import sys
import time
import warnings


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
        "GOES": "Geostationary Orbit Environment Satellite",
        "GPS\0": "Global Position System",
        "GAL\0": "Galileo Positioning System",
        "PPS\0": "Generic pulse-per-second",
        "IRIG": "Inter-Range Instrumentation Group",
        "WWVB": "LF Radio WWVB Ft. Collins, CO 60 kHz",
        "DCF\0": "LF Radio DCF77 Mainflingen, DE 77.5 kHz",
        "HBG\0": "LF Radio HBG Prangins, HB 75 kHz",
        "MSF\0": "LF Radio MSF Anthorn, UK 60 kHz",
        "JJY\0": "LF Radio JJY Fukushima, JP 40 kHz, Saga, JP 60 kHz",
        "LORC": "MF Radio LORAN C station, 100 kHz",
        "TDF\0": "MF Radio Allouis, FR 162 kHz",
        "CHU\0": "HF Radio CHU Ottawa, Ontario",
        "WWV\0": "HF Radio WWV Ft. Collins, CO",
        "WWVH": "HF Radio WWVH Kauai, HI",
        "NIST": "NIST telephone modem",
        "ACTS": "NIST telephone modem",
        "USNO": "USNO telephone modem",
        "PTB\0": "European telephone modem",
        "LOCL": "uncalibrated local clock",
        "CESM": "calibrated Cesium clock",
        "RBDM": "calibrated Rubidium clock",
        "OMEG": "OMEGA radionavigation system",
        "DCN\0": "DCN routing protocol",
        "TSP\0": "TSP time protocol",
        "DTS\0": "Digital Time Service",
        "ATOM": "Atomic clock (calibrated)",
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
COARSE_CLOCK_THRESHOLD = 0.001
# A raised Windows timer tick is ~1 ms; the default is ~15.6 ms. Anything
# at or under 2 ms means the tick has been raised (or the platform has no
# such tick), which is all this flag is asked to distinguish.
COARSE_SLEEP_THRESHOLD = 0.002


def precise_system_time():
    """Return the wall clock used to timestamp local NTP packet I/O.

    This one *must* branch. ``GetSystemTimePreciseAsFileTime`` has no POSIX
    counterpart, and it does not need one: ``time.time()`` is already
    ``clock_gettime(CLOCK_REALTIME)`` at microsecond resolution on macOS and
    Linux. Only Windows before Python 3.13 reads a 15.6 ms system tick here.
    """
    if not WINDOWS:
        return time.time()
    return _windows_precise_system_time()


def monotonic_time():
    """Return the monotonic clock used for EGI elapsed-time coordinates.

    Deliberately unbranched. On macOS and Linux CPython builds
    ``perf_counter()`` and ``monotonic()`` from the same underlying source
    (``mach_absolute_time()`` and ``clock_gettime(CLOCK_MONOTONIC)``
    respectively), so this is a no-op there -- but it means the clock the
    package uses on Windows is also the clock every test exercises on a
    developer machine. A Windows-only clock path is a path that only gets
    tested on the rig, which is how a 15.6 ms tick reaches a recording.

    The epoch of ``perf_counter()`` is unspecified and differs from that of
    ``time.monotonic()``, so readings from the two cannot be mixed. Only
    differences against :attr:`NetStation._sync_monotonic` are ever taken.
    """
    return time.perf_counter()


def clock_resolution(clock=monotonic_time, limit=0.05, samples=5):
    """Measure the effective resolution of ``clock`` in seconds.

    ``time.get_clock_info()`` reports what CPython believes it configured,
    which on Windows before 3.13 is not what a coarse system tick actually
    delivers. This collects several positive transitions and returns the
    smallest observed step, so an ordinary scheduling delay is less likely
    to be mistaken for the clock resolution. A 15.6 ms tick is visible as
    15.6 ms rather than as a claim of nanoseconds. Returns ``None`` if
    nothing changed within ``limit`` seconds, which is itself a diagnosis.
    """
    if samples < 1:
        raise ValueError('samples must be at least 1')

    previous = clock()
    steps = []
    deadline = time.perf_counter() + limit
    while time.perf_counter() < deadline:
        current = clock()
        step = current - previous
        if step > 0:
            steps.append(step)
            previous = current
            if len(steps) >= samples:
                break
        elif step < 0:
            # A wall clock can be disciplined while this diagnostic runs.
            # Reset the comparison point rather than reporting a negative
            # resolution or waiting for it to catch up.
            previous = current
    return min(steps) if steps else None


def sleep_granularity(requested=0.001, samples=3):
    """Measure the shortest sleep this process can actually take, in seconds.

    Windows sleeps are rounded up to the system timer tick, which is
    ~15.6 ms by default and ~1 ms once some process in the session has
    called ``timeBeginPeriod``. Unlike the clock resolutions above this
    does not corrupt any timestamp -- ``perf_counter`` and
    ``GetSystemTimePreciseAsFileTime`` are QPC-derived and tick
    independent -- but it does stretch everything that waits:
    ``drift_sample_spacing`` between NTP queries in a burst, the
    background sampler's wakeups, and the event sender's queue waits. A
    50 ms spacing becomes 62.5 ms on a coarse tick, so the pause budget
    an experiment reasons about is wrong by more than it expects.

    Reported, not warned about, and takes the minimum of a few trials so
    one descheduled trial does not stand in for the tick. Costs about
    ``samples`` ticks, so ~50 ms on the machine where it matters and
    microseconds everywhere else.

    See also ``python -m egi_pynetstation.check_clocks --compare``, which
    measures this against PsychoPy and timeBeginPeriod() to say *what*
    should raise the resolution. This probe records what the session
    actually got.
    """
    if samples < 1:
        raise ValueError('samples must be at least 1')
    slept = []
    for _ in range(samples):
        start = time.perf_counter()
        time.sleep(requested)
        slept.append(time.perf_counter() - start)
    return min(slept)


def clock_report():
    """Describe the clocks this process will actually use."""
    report = {
        'platform': sys.platform,
        'python_version': sys.version.split()[0],
        'capture_clock': 'perf_counter',
        'measured_capture_resolution': clock_resolution(),
        'measured_system_resolution': clock_resolution(precise_system_time),
        'measured_sleep_granularity': sleep_granularity(),
    }
    for name in ('perf_counter', 'monotonic', 'time'):
        try:
            info = time.get_clock_info(name)
        except Exception as err:
            report[f'{name}_info_error'] = f'{type(err).__name__}: {err}'
            continue
        report[f'{name}_implementation'] = info.implementation
        report[f'{name}_reported_resolution'] = info.resolution
    for name in ('capture', 'system'):
        measured = report[f'measured_{name}_resolution']
        report[f'{name}_clock_ok'] = (
            measured is not None and measured <= COARSE_CLOCK_THRESHOLD
        )
    report['clocks_ok'] = (
        report['capture_clock_ok'] and report['system_clock_ok']
    )
    # Deliberately outside clocks_ok, and check_clock_resolution() does not
    # warn on it. A coarse tick makes waits imprecise; it does not put a
    # wrong number in an event timestamp, and warning about it would train
    # people to ignore a warning that does mean corrupted data.
    report['sleep_granularity_ok'] = (
        report['measured_sleep_granularity'] <= COARSE_SLEEP_THRESHOLD
    )
    return report


def check_clock_resolution():
    """Return a clock report and warn if either timing clock is too coarse."""
    report = clock_report()
    bad_clocks = [
        name for name in ('capture', 'system')
        if not report[f'{name}_clock_ok']
    ]
    if not bad_clocks:
        return report

    details = []
    for name in bad_clocks:
        measured = report[f'measured_{name}_resolution']
        resolution = (
            '>%.0f ms' % (COARSE_CLOCK_THRESHOLD * 1000)
            if measured is None else '%.1f ms' % (measured * 1000)
        )
        details.append(f'{name}={resolution}')
    message = (
        'The measured clock resolution is too coarse for millisecond event '
        'timing (' + ', '.join(details) + '). Event timestamps or NTP offsets '
        'will be quantised and drift correction may never engage. On Windows '
        'this usually means the precise clock path is not in use.'
    )
    report['warning'] = message
    warnings.warn(message, RuntimeWarning, stacklevel=2)
    return report


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
            query_data = query_packet.to_data()
            sent_packet = NTPPacket()
            sent_packet.from_data(query_data)
            sock.sendto(query_data, sockaddr)

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
        _validate_response(stats, sent_packet.tx_timestamp)
        stats.dest_timestamp = dest_timestamp
        stats.local_time = dest_system
        stats.monotonic_time = dest_monotonic
        stats.python_monotonic_time = python_monotonic
        return stats


def _validate_response(stats, expected_orig_timestamp):
    """Reject NTP replies that cannot safely anchor the local clock."""
    if stats.mode != 4:
        raise NTPException(
            'Invalid NTP response mode %s; expected server mode 4.'
            % stats.mode
        )
    if stats.leap == 3:
        raise NTPException('NTP server reports an unsynchronized clock.')
    if not 1 <= stats.stratum <= 15:
        raise NTPException(
            'Invalid or unsynchronized NTP stratum %s.' % stats.stratum
        )
    if stats.orig_timestamp != expected_orig_timestamp:
        raise NTPException(
            'NTP response originate timestamp does not match the request.'
        )
    if stats.tx_timestamp == 0:
        raise NTPException('NTP response has no transmit timestamp.')


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
