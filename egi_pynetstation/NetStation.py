#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Abstraction of the NetStation SDK as an object"""

import atexit
import binascii
import functools
import json
import logging
import queue
import socket
import sys
import threading
import time
import warnings
from pathlib import Path
from typing import Union

from .eci import (
    build_command, parse_response, frame_response, response_shape,
    allowed_endians,
    package_event,
)
from .egi_ntp import (
    system_to_ntp_time, NTPClient,
    clock_report as ntp_clock_report,
    check_clock_resolution as check_ntp_clock_resolution,
    monotonic_time as ntp_monotonic_time,
)
from .socket_wrapper import Socket
from .exceptions import *

cyan = '\u001b[36;1m'
reset = '\u001b[0m'
logger = logging.getLogger(__name__)

# Drift slopes are carried internally as seconds of offset change per
# second of elapsed time. Everything human-facing wants ms/hour.
MS_PER_HOUR = 1000.0 * 3600.0


# time.monotonic() is GetTickCount64 on Windows before Python 3.13, which
# advances in ~15.6 ms steps. A caller's reading is quantised before the
# package ever sees it, so time_at_monotonic() cannot repair it -- warn once
# rather than on every event.
_COARSE_MONOTONIC = (
    sys.platform == 'win32' and sys.version_info < (3, 13)
)
_warned_coarse_monotonic = False


def _warn_coarse_monotonic():
    """Warn the first time a coarse time.monotonic() reading is converted."""
    global _warned_coarse_monotonic
    if not _COARSE_MONOTONIC or _warned_coarse_monotonic:
        return
    _warned_coarse_monotonic = True
    warnings.warn(
        'time_at_monotonic() is deprecated and cannot be accurate on '
        'Windows before Python 3.13: time.monotonic() advances in ~15.6 ms '
        'steps there, so the reading passed in was already quantised. '
        'Capture with NetStation.capture_time() and convert with '
        'time_at_capture() instead.',
        FutureWarning,
        stacklevel=3,
    )


def slope_ms_per_hour(slope):
    """Convert a drift slope to milliseconds per hour, tolerating None.

    Most call sites are reporting a slope that may not exist yet, so the
    None check lives here rather than being repeated at each one.
    """
    return None if slope is None else slope * MS_PER_HOUR


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

    ``endian`` is the legacy ECI ``Qcccc`` byte-order token, not a modern
    CPU or operating-system name. ``NTEL`` means little-endian and is the
    correct default for both Intel and Apple silicon Macs. ``MAC-`` and
    ``UNIX`` are the protocol's historical big-endian tokens; big-endian
    operation has not been tested by this package.
    """
    def __init__(
        self,
        ipv4: str,
        port: int,
        endian: str = 'NTEL',
        debug: bool = False,
        error_log: str = None,
    ) -> None:
        """Constructor for NetStation

        Parameters
        ----------
        ipv4 : str
            The IPv4 address to use for the amplifier.
        port : int
            The port number to use for the amplifier.
        endian : str
            Legacy ECI byte-order token. ``NTEL`` means little-endian and
            is correct for all current Macs. ``MAC-`` and ``UNIX`` mean
            big-endian and are retained for protocol compatibility; see
            ``eci.allowed_endians``.
        debug : bool
            Print ECI command and response bytes when True.
        error_log : str, optional
            Path for JSON-lines ECI error records. See the Diagnostics
            page for the record formats.


        See Also
        --------
        eci.eci: module for parsing eci commands/responses
        """
        self._socket = Socket(ipv4, port)
        # Two locks, never held in the reverse order. _io_lock guards the
        # socket and the response-token buffer; _clock_lock guards drift
        # state. getTime() takes only _clock_lock, so an event send in a
        # worker thread can never stall a timestamp read on the render
        # thread. Where both are needed they are taken sequentially, not
        # nested, except that _clock_lock may be taken while _io_lock is
        # held (never the reverse).
        self._io_lock = threading.RLock()
        self._clock_lock = threading.RLock()
        self._connected = False
        if not (endian in allowed_endians):
            raise NetStationIllegalArgument(endian)
        self._endian = endian
        self._debug = debug
        self._error_log = error_log
        self._syncepoch = None
        self._offset_mono = None
        self._sync_monotonic = None
        # Skew from time.monotonic() into the package capture clock, so the
        # deprecated time_at_monotonic() can be a shim rather than a second
        # implementation. Everything else knows about one frame only.
        self._capture_minus_monotonic = None
        self._sync_system_time = None
        self._client_clock_start_ntp = None
        self._server_clock_start_ntp = None
        self._clock_start_history = []
        self._drift_history = []
        self._drift_correction = True
        # A long baseline protects the slope estimate, independently of how
        # precise each individual clock reading is. Keep these conservative
        # until shorter windows have been validated on both Windows and macOS.
        self._drift_min_samples = 13
        self._drift_min_span = 180.0
        self._drift_max_delay = 0.010
        self._drift_max_residual = 0.003
        self._drift_window = 15 * 60.0
        self._drift_max_model_age = 600.0
        self._drift_slew = 0.0002
        self._drift_samples_per_call = 4
        self._drift_sample_spacing = 0.05
        # Per-query NTP timeout. The client default is 5 s, so a 4-query
        # burst against a dead server could block ~20 s -- longer than
        # anything waiting on the sampler thread expects to wait.
        self._ntp_timeout = 2.0
        self._drift_model = None
        self._drift_model_dirty = True
        self._drift_active_model = None
        self._drift_last_reject_reason = None
        self._drift_rejected_fits = 0
        self._drift_accepted_fits = 0
        self._drift_consecutive_rejections = 0
        self._drift_stall_after = 5
        self._drift_stalled = False
        self._drift_stall_started_elapsed = None
        # Drift-transition log records noticed under _clock_lock, parked
        # here so the filesystem write happens outside the lock.
        self._pending_log_records = []
        # On by default so that sample_drift_if_due() -- the call an
        # experiment is told to make -- actually takes samples. Sampling on a
        # schedule is inert until something asks for a sample, so the default
        # costs nothing for experiments that never call it.
        self._auto_drift_enabled = True
        self._auto_drift_interval = 15.0
        self._auto_drift_min_pause = 0.35
        self._auto_drift_last_monotonic = None
        # Background sampling is the default: it needs no cooperation from
        # the experiment, and every direct comparison run so far favoured
        # it over cooperative sampling (which fails silently -- and once,
        # for a whole hour -- if sample_drift_if_due() is never called or
        # miscomputes its available-pause budget). Cooperative sampling
        # remains available for advanced use: experiments that want tight
        # control over exactly when NTP queries happen, e.g. to guarantee
        # they never coincide with a specific critical window.
        self._auto_drift_background = True
        self._auto_drift_thread = None
        self._auto_drift_stop = threading.Event()
        # Bytes received but not yet consumed by a response. ECI has no
        # length prefix, so a response split across two recv() calls can
        # only be reassembled by keeping the remainder here. Guarded by
        # _io_lock, like everything else touching the socket.
        self._rx_buffer = b''
        self._recording_start = None
        # Whether a recording epoch has already been used on this
        # connection. begin_rec() re-runs the ECI clock sync, which
        # re-bases the local event epoch while the drift model still holds
        # samples measured against the previous one -- so a second epoch
        # is refused rather than silently mixing two coordinate systems.
        self._recording_started = False
        # Whether the ECI clock sync that establishes the event timestamp
        # epoch has completed. Set only after the amplifier accepts it.
        self._ntpsynced = False
        # Background event sender. send_event() captures the timestamp on
        # the calling thread and hands the socket write to this worker, so
        # a screen-flip callback is never blocked by network I/O.
        # Guards the lifecycle of both background threads (event sender
        # and drift sampler). They are the same start-once/stop-once
        # problem twice, so they share one lock rather than each
        # inventing its own discipline.
        self._thread_lock = threading.RLock()
        # Serialises writes to the JSON-lines error log. Both background
        # threads and the caller's own thread can produce records, and an
        # interleaved write would corrupt the file. This is a leaf lock:
        # nothing else is ever acquired while it is held, so it cannot
        # take part in a deadlock.
        self._log_lock = threading.Lock()
        self._event_queue = None
        self._event_thread = None
        self._event_stop = object()
        self._event_errors = []
        # ECI responses that failed to parse or that the amplifier
        # rejected. By default these are recorded rather than raised, so a
        # single garbled reply cannot end a recording; set strict_eci=True
        # to have them raise instead. Kept bounded -- the error log holds
        # the full history, this is just what stays handy at runtime.
        self._strict_eci = False
        self._eci_errors = []
        self._eci_error_count = 0
        self._eci_errors_kept = 100
        # NTP sampling health. A total sampling outage is otherwise
        # invisible: no sample means no fit is attempted, so the stall
        # detector -- which counts *rejected* fits -- never fires, and the
        # session keeps reporting itself healthy while the applied
        # correction silently goes stale.
        self._ntp_failure_count = 0
        self._ntp_consecutive_failures = 0
        self._ntp_last_attempt_monotonic = None
        self._ntp_last_success_monotonic = None
        self._ntp_last_error = None
        # Transition flag, so a long outage logs on entry and on recovery
        # rather than once every interval.
        self._ntp_failure_reported = False
        self._ntp_failures_before_report = 3

    def check_connected(func) -> None:
        """Decorator to raise exception if not connected

        Parameters
        ----------
        func : callable
            The method to guard.

        Raises
        ------
        NetStationUnconnected
            If NetStation hasn't had .connect() run yet

        Notes
        -----
        functools.wraps matters here: without it every decorated public
        method would report itself as ``wrapper(*args, **kwargs)`` with no
        docstring, which hides the entire API from help(), IDEs, and the
        Sphinx documentation build.
        """
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if args[0]._connected:
                try:
                    return func(*args, **kwargs)
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

    def set_debug(self, debug: bool = True) -> None:
        """Enable or disable debug output for ECI traffic."""
        self._debug = debug

    def set_error_log(self, path: str = None) -> None:
        """Set a JSON-lines log path for ECI response errors."""
        self._error_log = path

    def connect(
        self,
        clock: str = 'ntp',
        ntp_ip: str = None,
        handshake: bool = True,
        drift_correction: bool = True,
        drift_min_samples: int = None,
        drift_min_span: float = None,
        drift_max_delay: float = None,
        drift_max_residual: float = None,
        drift_window_minutes: float = None,
        drift_samples: int = None,
        drift_sample_spacing: float = None,
        drift_slew: float = None,
        drift_max_model_age: float = None,
        auto_drift: bool = None,
        auto_drift_interval: float = None,
        auto_drift_min_pause: float = None,
        auto_drift_background: bool = None,
        strict_eci: bool = None,
    ) -> None:
        """Connect to the Netstation machine via TCP/IP

        Parameters
        ----------
        clock : str
            Either ``'ntp'`` or ``'simple'``, indicating the clock sync
            method. Only ``'ntp'`` is currently implemented.
        ntp_ip : str
            The IPv4 address of the NTP server on the amplifier.
        handshake : bool
            Send Query and Attention immediately after connecting.
        drift_correction : bool
            Enable client-side drift correction for :meth:`getTime`.
        drift_min_samples : int, optional
            Minimum number of NTP samples needed before applying drift
            correction.
        drift_min_span : float, optional
            Minimum sample window, in seconds, needed before applying
            drift correction.
        drift_max_delay : float, optional
            Maximum NTP round-trip delay, in seconds, for samples used by
            the drift model.
        drift_max_residual : float, optional
            Maximum absolute model residual, in seconds, before a fitted
            drift line is rejected.
        drift_window_minutes : float, optional
            Rolling window, in minutes, used by the drift model. Use 0 to
            fit all valid drift samples.
        drift_samples : int, optional
            Number of NTP queries per drift sample; the lowest-delay reply
            is kept.
        drift_sample_spacing : float, optional
            Seconds between queries within one burst.
        drift_slew : float, optional
            Maximum rate, in seconds of correction per second of elapsed
            time, at which level errors are retired.
        drift_max_model_age : float, optional
            Maximum seconds a fitted slope may be extrapolated past its
            anchor. Use 0 for unbounded extrapolation.
        auto_drift : bool, optional
            Enable automatic NTP drift sampling. On by default; pass False
            to disable it. Equivalent to calling
            :meth:`configure_auto_drift` after connecting. Passing any
            other ``auto_drift_*`` argument implies this is True.

            Note that this enables the *schedule*, not necessarily the
            experiment's own sampling calls. With the default
            ``auto_drift_background=True``, the package samples on its own
            thread and no cooperation is needed. Pass
            ``auto_drift_background=False`` to sample only when the
            experiment calls :meth:`sample_drift_if_due` instead.
        auto_drift_interval : float, optional
            Target seconds between drift samples.
        auto_drift_min_pause : float, optional
            Minimum idle time an experiment must offer before a
            cooperative sample is taken.
        auto_drift_background : bool, optional
            Sample from a package-owned thread instead of requiring
            :meth:`sample_drift_if_due` calls. True by default. See
            :meth:`configure_auto_drift` for when False is worth choosing
            instead.
        strict_eci : bool, optional
            Whether a failed ECI response raises. False by default: the
            failure is written to the error log and kept in
            :meth:`eci_errors` instead, so one garbled or rejected reply
            cannot end a recording. Pass True for a diagnostic session
            where stopping at the first sign of trouble is preferable.

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

        # A NetStation object may be reconnected after disconnect().
        # Clearing here rather than in disconnect() means state survives
        # for inspection after a run and is only discarded when a genuinely
        # new session begins.
        self._reset_session_state()
        self._socket.connect()
        self._connected = True
        # Collected by name rather than passed as seventeen positional
        # arguments. Every value stays keyword-matched from here to the
        # setter that consumes it, so adding an option cannot silently
        # transpose two existing ones -- which a positional call across a
        # line break made easy and invisible.
        drift_options = {
            'min_samples': drift_min_samples,
            'min_span': drift_min_span,
            'max_delay': drift_max_delay,
            'max_residual': drift_max_residual,
            'window_minutes': drift_window_minutes,
            'samples': drift_samples,
            'spacing': drift_sample_spacing,
            'slew': drift_slew,
            'max_model_age': drift_max_model_age,
            'auto_enabled': auto_drift,
            'auto_interval': auto_drift_interval,
            'auto_min_pause': auto_drift_min_pause,
            'auto_background': auto_drift_background,
        }
        try:
            self._configure_and_handshake(
                ntp_ip, handshake, drift_correction, strict_eci,
                drift_options,
            )
        except Exception:
            # Setup is all-or-nothing. A rejected handshake or a bad drift
            # argument used to leave an open socket, a live sampler
            # thread, and _connected set, so the object looked usable and
            # the next call failed somewhere less obvious.
            self._abort_connect()
            raise

    def _abort_connect(self) -> None:
        """Undo a partially completed connect()."""
        try:
            self._stop_auto_drift_thread()
            self._stop_event_sender()
        except Exception as err:
            logger.error(
                'While cleaning up a failed connect(): %s: %s',
                type(err).__name__, err,
            )
        try:
            self._socket.disconnect()
        except Exception as err:
            logger.error(
                'While closing the socket after a failed connect(): '
                '%s: %s', type(err).__name__, err,
            )
        self._connected = False
        self._ntp_ip = None
        self._reset_session_state()

    # Every key connect() may place in its drift_options dict. Listed here
    # so a typo in either the producer or a consumer is caught at connect
    # time by the check below, rather than silently leaving a setting at
    # its default for the whole session.
    _DRIFT_OPTION_KEYS = frozenset({
        'min_samples', 'min_span',
        'max_delay', 'max_residual', 'window_minutes',
        'samples', 'spacing',
        'slew', 'max_model_age',
        'auto_enabled', 'auto_interval', 'auto_min_pause', 'auto_background',
    })

    def _configure_and_handshake(
        self,
        ntp_ip, handshake, drift_correction, strict_eci,
        drift_options: dict,
    ) -> None:
        """The part of connect() that must succeed as a unit.

        ``drift_options`` carries the drift settings by name; see
        ``_DRIFT_OPTION_KEYS``. Values are passed through untouched, so
        None still means "leave this setting unchanged" at every setter.
        """
        unknown = set(drift_options) - self._DRIFT_OPTION_KEYS
        if unknown:
            raise KeyError(
                f'unknown drift option(s): {sorted(unknown)}'
            )
        missing = self._DRIFT_OPTION_KEYS - set(drift_options)
        if missing:
            raise KeyError(
                f'missing drift option(s): {sorted(missing)}'
            )

        self._ntp_ip = ntp_ip
        self._drift_correction = drift_correction
        if strict_eci is not None:
            self.set_strict_eci(strict_eci)
        # Every setter below is called unconditionally. They all treat None
        # as "leave unchanged", so there is nothing to gate on -- and the
        # gates are what caused three separate silent-config bugs, where an
        # argument was dropped because a *sibling* argument was absent from
        # the guard. Do not reintroduce conditionals here.
        self.set_drift_requirements(
            min_samples=drift_options['min_samples'],
            min_span=drift_options['min_span'],
        )
        self.set_drift_model_options(
            max_delay=drift_options['max_delay'],
            max_residual=drift_options['max_residual'],
            window_minutes=drift_options['window_minutes'],
        )
        self.set_drift_sampling(
            samples=drift_options['samples'],
            spacing=drift_options['spacing'],
        )
        self.set_drift_stability(
            slew=drift_options['slew'],
            max_model_age=drift_options['max_model_age'],
        )
        # configure_auto_drift() also starts the background sampler as a
        # side effect, so it must run even when no argument was given.
        self.configure_auto_drift(
            enabled=drift_options['auto_enabled'],
            interval=drift_options['auto_interval'],
            min_pause=drift_options['auto_min_pause'],
            background=drift_options['auto_background'],
        )
        # Start the sender up front so the first send_event() -- which may
        # well be inside a flip callback -- does not pay the thread-start
        # cost. Go through _ensure_event_sender() rather than calling
        # _start_event_sender() directly: the latter is unsynchronized, so
        # a send_event() racing connect() could start a second sender and
        # orphan the first on a queue nobody writes to.
        self._ensure_event_sender()
        # Its own statement, not an argument inside the dict below. This
        # call is also what warms the Windows precise-clock path -- the
        # first precise_system_time() imports ctypes, loads kernel32 and
        # resolves GetSystemTimePreciseAsFileTime -- and that cost belongs
        # here rather than on the first NTP query in begin_rec(). Inlined
        # it would still run (a dict argument is evaluated before
        # _append_error_log() can early-return on a missing log file), but
        # only by accident, and the next person to tidy the dict would
        # silently drop both the warm-up and the coarse-clock warning.
        clocks = self._check_clock_resolution()
        # A header line so a log file is self-describing: which amplifier,
        # which settings, which package version produced these records.
        self._append_error_log({
            'record': 'session_start',
            'ntp_ip': ntp_ip,
            'drift_correction': self._drift_correction,
            'drift_min_samples': self._drift_min_samples,
            'drift_min_span': self._drift_min_span,
            'drift_max_delay': self._drift_max_delay,
            'drift_max_residual': self._drift_max_residual,
            'drift_window': self._drift_window,
            'drift_samples_per_call': self._drift_samples_per_call,
            'drift_slew': self._drift_slew,
            'drift_max_model_age': self._drift_max_model_age,
            'clocks': clocks,
            'clock': None,
        })
        if handshake:
            # Strict: if the amplifier will not complete the opening
            # handshake there is no working session, and every later
            # command would fail in a more confusing way.
            self._command('Query', self._endian, strict=True)
            self._command('Attention', strict=True)

    def _reset_session_state(self) -> None:
        """Discard everything tied to a previous connection's clock epoch.

        Drift samples carry elapsed times measured from a specific sync
        origin. Reconnecting establishes a new one, so carrying the old
        history over would fit a line through two different coordinate
        systems -- the failure mode that makes a second recording epoch
        unsafe in the first place.
        """
        with self._clock_lock:
            self._syncepoch = None
            self._sync_system_time = None
            self._sync_monotonic = None
            self._capture_minus_monotonic = None
            self._offset = None
            self._offset_mono = None
            self._client_clock_start_ntp = None
            self._server_clock_start_ntp = None
            self._clock_start_history = []
            self._drift_history = []
            self._drift_model = None
            self._drift_model_dirty = True
            self._drift_active_model = None
            self._drift_last_reject_reason = None
            self._drift_rejected_fits = 0
            self._drift_accepted_fits = 0
            self._drift_consecutive_rejections = 0
            self._drift_stalled = False
            self._drift_stall_started_elapsed = None
            self._auto_drift_last_monotonic = None
            self._pending_log_records = []
            self._recording_start = None
            self._recording_started = False
            self._ntpsynced = False
            self._event_errors = []
            self._eci_errors = []
            self._eci_error_count = 0
            self._ntp_failure_count = 0
            self._ntp_consecutive_failures = 0
            self._ntp_last_attempt_monotonic = None
            self._ntp_last_success_monotonic = None
            self._ntp_last_error = None
            self._ntp_failure_reported = False
        # Half-read bytes from the previous socket must not be attributed
        # to commands sent on the new one.
        with self._io_lock:
            self._rx_buffer = b''

    @check_connected
    def ntpsync(self, force: bool = False):
        """Perform the ECI NTP synchronization.

        This sends one ECI ``NTPClockSync`` command using the current offset
        reported by the amplifier/Net Station NTP server. It also stores the
        first NTP offset sample used by the client-side drift corrector.
        Repeated ECI clock syncs during a recording can reset the local event
        timestamp epoch and should be avoided for normal experiments, so a
        second call is refused unless ``force=True``.

        Parameters
        ----------
        force : bool
            Re-run the sync even though one has already completed. This
            re-bases the event timestamp epoch mid-recording and
            invalidates the elapsed-time coordinates every existing drift
            sample was measured against. Diagnostic use only.

        Raises
        ------
        NetStationLifecycleError
            If the sync has already completed and ``force`` is False.
        """
        if not self._ntp_ip:
            raise NetStationNoNTPIP()
        if self._ntpsynced and not force:
            raise NetStationLifecycleError(
                'The ECI clock sync has already been performed. Repeating '
                'it re-bases the event timestamp epoch, so every drift '
                'sample already collected would be measured against a '
                'different origin. begin_rec() performs the one sync an '
                'experiment needs. Pass force=True only for diagnostics.'
            )
        c = NTPClient()
        response = c.request(self._ntp_ip, version=3)
        # No getattr() fallback to time.time()/time.monotonic() here. The
        # NTP client is vendored, so request() always attaches these; a
        # fallback could only fire for a foreign stats object, and would
        # then substitute a clock reading taken at the wrong instant, in a
        # possibly different frame, into the offset that anchors the whole
        # recording. That is the failure this module exists to prevent, so
        # let it raise instead.
        t = response.local_time
        monotonic_t = response.monotonic_time
        python_monotonic_t = response.python_monotonic_time
        ntp_t = system_to_ntp_time(t + response.offset)
        with self._io_lock:
            # Strict, and _ntpsynced is set only after the amplifier
            # accepts. This establishes the event timestamp epoch for the
            # whole recording; a silently failed sync would put every
            # marker in the file at the wrong time.
            self._command('Attention', strict=True)
            cresponse = self._command('NTPClockSync', ntp_t, strict=True)
            self._ntpsynced = True
        with self._clock_lock:
            # _offset stays in the system-clock frame for ECI/NTP timecode
            # use. _offset_mono is the monotonic-frame reference the drift
            # corrector and getTime() compare against; the two frames must
            # never be mixed.
            self._offset = response.offset
            self._offset_mono = response.offset + (t - monotonic_t)
            self._syncepoch = t
            self._sync_system_time = t
            self._sync_monotonic = monotonic_t
            self._capture_minus_monotonic = monotonic_t - python_monotonic_t
            self._client_clock_start_ntp = ntp_t
            self._record_ntp_drift_sample(
                response,
                source='ntpsync',
                local_time=t,
                monotonic_time=monotonic_t,
            )
        self._flush_pending_log_records()
        return cresponse

    @check_connected
    def resync(self, attention: bool = False):
        """Deprecated alias for :meth:`sync_return_clock`.

        .. deprecated:: 2.0
           There is no longer a reason to call this. It once meant
           "refresh the clock", but drift is corrected continuously now,
           and what it actually runs -- :meth:`sync_return_clock` -- is a
           diagnostic that can write ``resy`` markers into the recording
           and holds the ECI socket across several round trips.

        Raises
        ------
        RuntimeError
            If automatic background drift sampling is running. In that
            configuration the package is already keeping the clock model
            current, so this call is redundant as well as disruptive.
            Call :meth:`sync_return_clock` directly if you genuinely need
            the server clock-start estimate.
        """
        with self._clock_lock:
            background_sampling = (
                self._auto_drift_enabled and self._auto_drift_background
            )
        if background_sampling:
            raise RuntimeError(
                'resync() does nothing useful while background drift '
                'sampling is running -- the clock model is already being '
                'kept current -- and it can write resy markers into the '
                'recording. Remove the call. If you specifically need the '
                'server clock-start estimate, call sync_return_clock().'
            )
        warnings.warn(
            'resync() is deprecated and unnecessary: drift is corrected '
            'continuously. It forwards to sync_return_clock(), a '
            'diagnostic that may write resy markers into the recording. '
            'Call sync_return_clock() directly if that is what you want.',
            DeprecationWarning,
            stacklevel=2,
        )
        return self.sync_return_clock(attention=attention)

    @check_connected
    def sync_return_clock(
        self,
        attention: bool = False,
        max_followups: int = 3,
    ):
        """Update the server clock-start estimate.

        Net Station appears to return the NTPReturnClock timestamp on the
        following ECI response. To account for that behavior, this sends
        NTPReturnClock and then sends resy events until a timestamp is
        returned, using that timestamp as the amplifier/server clock start.

        .. warning::

           This is a diagnostic, not something to call during a recording.
           Two reasons. It **writes real ``resy`` event markers** into the
           recording (up to ``max_followups`` of them) whenever the
           amplifier does not answer inline, which is the common case. And
           it holds the ECI socket lock across every one of those round
           trips, so asynchronous event sends queued meanwhile cannot go
           out until it finishes.

           Drift correction does not need this. It is handled continuously
           by :meth:`sample_drift` and the drift model.

        Parameters
        ----------
        attention : bool
            Send Attention immediately before NTPReturnClock. Defaults to
            False because some Net Station configurations appear to
            suppress the delayed timestamp response after Attention.
        max_followups : int
            Number of ``resy`` events to send while waiting for the
            delayed timestamp.
        """
        with self._io_lock:
            if not self._ntp_ip:
                raise NetStationNoNTPIP()
            if self._client_clock_start_ntp is None:
                raise RuntimeError(
                    'sync_return_clock is unavailable before NTP sync'
                )
            if attention:
                self._command('Attention')
            response = self._command(
                'NTPReturnClock',
                self._client_clock_start_ntp,
            )
            if isinstance(response, float):
                self._update_server_clock_start(
                    response,
                    time.time(),
                    source='return_clock',
                )
                return response

            last_response = response
            for _ in range(max_followups):
                event_response = self.send_event(
                    event_type="resy", label='resy', wait=True
                )
                if isinstance(event_response, float):
                    self._update_server_clock_start(
                        event_response,
                        time.time(),
                        source='return_clock_followup',
                    )
                    return event_response
                last_response = event_response
            return last_response

    @check_connected
    def getTime(self):
        """Return the current event timestamp in seconds.

        The timestamp is measured from the most recent ``ntpsync()`` using a
        monotonic local clock. When drift correction is enabled, this method
        adds the predicted change in NTP offset between the client computer and
        the amplifier/Net Station NTP server. The correction is not applied
        until the drift history satisfies ``set_drift_requirements()``.

        See Also
        --------
        capture_time : Capture the package's high-resolution clock.
        time_at_capture : Convert a package clock reading into an event
            timestamp.
        """
        return self.time_at_capture(self.capture_time())

    def capture_time(self):
        """Capture the package's high-resolution monotonic clock."""
        return ntp_monotonic_time()

    @check_connected
    def time_at_capture(self, captured_time: float):
        """Convert a value from :meth:`capture_time` to an event timestamp."""
        with self._clock_lock:
            if self._syncepoch is None:
                raise RuntimeError('getTime is unavailable before NTP sync')
            if self._sync_monotonic is None:
                raise RuntimeError(
                    'capture clock anchor is unavailable after NTP sync'
                )
            return self._time_at_elapsed(
                captured_time - self._sync_monotonic
            )

    @check_connected
    def time_at_monotonic(self, monotonic_time: float):
        """Convert a standard ``time.monotonic()`` reading.

        .. deprecated:: 2.1
            Use :meth:`capture_time` with :meth:`time_at_capture`. This
            method will be removed in 3.0.

        Parameters
        ----------
        monotonic_time:
            A value previously returned by ``time.monotonic()``.

        Notes
        -----
        Kept for the v2.0.0 API. On Windows before Python 3.13 this method
        *cannot* be made accurate: ``time.monotonic()`` is ``GetTickCount64``
        there, so the caller's reading was already quantised to a 15.6 ms
        tick before this method saw it. Nothing downstream can recover the
        lost precision, which is why the first use on that platform warns.
        """
        _warn_coarse_monotonic()
        with self._clock_lock:
            if self._syncepoch is None:
                raise RuntimeError('getTime is unavailable before NTP sync')
            skew = self._capture_minus_monotonic
        if skew is None:
            raise RuntimeError(
                'time.monotonic compatibility anchor is unavailable'
            )
        return self.time_at_capture(monotonic_time + skew)

    def _time_at_elapsed(self, elapsed: float):
        """Apply the active drift model to elapsed package-clock time."""
        if not self._drift_correction:
            return elapsed

        initial_offset = getattr(self, '_offset_mono', None)
        predicted_offset = self._predict_ntp_offset(elapsed)
        if initial_offset is None or predicted_offset is None:
            return elapsed
        return elapsed + (predicted_offset - initial_offset)

    @check_connected
    def disconnect(self) -> None:
        """Close the TCP/IP connection.

        Any queued asynchronous events are flushed before the socket closes.
        """
        self._stop_auto_drift_thread()
        self._flush_pending_log_records()
        self._warn_if_undersampled()
        self.flush_events()
        self._stop_event_sender()
        if self._event_errors:
            logger.warning(
                '%d asynchronous ECI event send(s) failed during this '
                'session; see event_errors() for details.',
                len(self._event_errors),
            )
        with self._io_lock:
            self._command('Exit')
            self._socket.disconnect()
            self._connected = False

    @check_connected
    def begin_rec(self) -> None:
        """Begin recording and perform the initial ECI NTP sync.

        Raises
        ------
        NetStationNoNTPIP
            If no NTP server address was given to :meth:`connect`.
        ECIResponseFailure
            If Net Station refuses or garbles the ``BeginRecording``
            command. Nothing local is changed in that case: there is no
            recording start time and no clock sync, so the failure cannot
            be mistaken for a working session.

        Notes
        -----
        Recording control is always strict, regardless of ``strict_eci``.
        That setting governs *event* responses, where dropping one marker
        is better than ending a run. This is the opposite situation: a
        rejected BeginRecording means there is no recording at all, and
        continuing produces the worst possible outcome -- a complete
        behavioural session with local logs but no EEG.
        """
        if not self._ntp_ip:
            raise NetStationNoNTPIP()
        if self._recording_started:
            raise NetStationLifecycleError(
                'This connection has already recorded. A second '
                'begin_rec() would re-run the ECI clock sync and re-base '
                'the event timestamp epoch, while the drift model still '
                'holds samples measured against the first one. Call '
                'disconnect() and create a new NetStation for the next '
                'recording.'
            )
        with self._io_lock:
            self._command('BeginRecording', strict=True)
            self._recording_started = True
            # Only now is there really a recording to timestamp against.
            self._recording_start = time.time()
            try:
                self.ntpsync(force=True)
            except Exception:
                # Recording did start, but the epoch is unusable. Say so
                # rather than leaving a half-initialised clock behind.
                self._recording_start = None
                raise

    @check_connected
    def end_rec(self) -> None:
        """End Recording

        Any queued asynchronous events are flushed first, so events sent
        just before this call still reach Net Station.
        """
        self.flush_events()
        with self._io_lock:
            # Strict for the same reason as begin_rec(): if Net Station
            # did not accept the stop, the operator needs to know before
            # they walk away from the amplifier.
            self._command('EndRecording', strict=True)
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
        wait: bool = False,
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
        wait: bool
            Whether to block until the amplifier answers. False by default,
            which is what makes send_event() safe to call from a screen-flip
            callback: it captures the timestamp on the calling thread and
            returns in microseconds while a worker does the socket write.
            A non-blocking send returns None, since there is no response
            yet; failures are recorded in ``event_errors()``. Pass True when
            you actually need the parsed ECI response back, at the cost of a
            full network round trip on the calling thread.

        Notes
        -----
        The default ``start="now"`` uses ``getTime()``. When NTP drift
        correction is enabled and enough drift samples have been collected,
        this means event timestamps are corrected on the client side before
        they are sent to Net Station.

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
        if not (start == 'now' or isinstance(start, (int, float))):
            t_start = type(start)
            # Raise, do not return. This used to return the exception
            # object, which is truthy, so the event was silently dropped
            # and no except-clause ever fired.
            raise TypeError(
                f'Start is type {t_start}, should be str "now" or float'
            )

        if not wait:
            # Capture the clock reading on THIS thread -- the one that is
            # aligned with the stimulus -- before anything else. This call
            # is the one that may run in a flip callback, so it must not
            # touch a lock or the socket.
            monotonic_at_call = ntp_monotonic_time()
            self._ensure_event_sender()
            self._event_queue.put((
                monotonic_at_call,
                {
                    'start': None if start == 'now' else float(start),
                    'duration': duration,
                    'event_type': event_type,
                    'label': label,
                    'desc': desc,
                    'data': data,
                },
            ))
            return None

        # The timestamp is resolved before the socket lock is taken, so a
        # send in progress on another thread can never delay it.
        if start == 'now':
            start = self.getTime()
        return self._send_event_now(
            start=float(start),
            duration=duration,
            event_type=event_type,
            label=label,
            desc=desc,
            data=data,
        )

    def _send_event_now(
        self,
        start: float,
        duration: float = 0.001,
        event_type: str = ' ' * 4,
        label: str = ' ' * 4,
        desc: str = ' ' * 4,
        data: dict = {},
    ):
        """Package and write one event. Assumes ``start`` is already resolved."""
        packaged = package_event(
            start, duration, event_type, label, desc, data
        )
        with self._io_lock:
            return self._command(
                'EventData',
                packaged,
                context={
                    'event_type': event_type,
                    'label': label,
                    'start': start,
                },
            )

    def _ensure_event_sender(self) -> None:
        """Start the background sender if it is not already running.

        Double-checked so the common path -- a sender already started by
        connect() -- takes no lock. This lets wait=False work even when the
        connection default is synchronous.
        """
        existing = self._event_thread
        if existing is not None and existing.is_alive():
            return
        with self._thread_lock:
            self._start_event_sender()

    def _start_event_sender(self) -> None:
        # Callers hold _thread_lock (via _ensure_event_sender). Checking
        # is_alive() rather than "is not None" means a handle left behind
        # by a thread that has since exited gets replaced instead of
        # blocking all future sends.
        existing = self._event_thread
        if existing is not None and existing.is_alive():
            return
        # Asynchronous sending is the default, so an experiment that exits
        # without calling disconnect() must not silently lose whatever is
        # still queued. Daemon threads are still alive during atexit, so a
        # last-chance flush here is effective.
        atexit.register(self._flush_at_exit)
        self._event_queue = queue.Queue()
        self._event_thread = threading.Thread(
            target=self._event_sender_loop,
            name='eci-event-sender',
            daemon=True,
        )
        self._event_thread.start()

    def _event_sender_loop(self) -> None:
        while True:
            item = self._event_queue.get()
            try:
                if item is self._event_stop:
                    return
                monotonic_at_call, kwargs = item
                try:
                    if kwargs.get('start') is None:
                        kwargs['start'] = self.time_at_capture(
                            monotonic_at_call
                        )
                    self._send_event_now(**kwargs)
                except Exception as err:
                    # Never let one bad event kill the sender thread. Record
                    # it so the experiment can check event_errors() later.
                    record = {
                        'record': 'event_send_failure',
                        'time': time.time(),
                        'error': f'{type(err).__name__}: {err}',
                        'event_type': kwargs.get('event_type'),
                        'label': kwargs.get('label'),
                        'start': kwargs.get('start'),
                    }
                    with self._clock_lock:
                        self._event_errors.append(dict(record))
                    logger.error(
                        'Asynchronous ECI event send failed: %s',
                        record['error'],
                    )
                    # This is the failure an experiment is most likely to
                    # hit, and it cannot be raised to the caller, so it has
                    # to reach the error log.
                    self._append_error_log(record)
            finally:
                self._event_queue.task_done()

    def _drain_queue(self, timeout: float = None) -> bool:
        """Wait until every queued event has actually been sent.

        Waits on the queue's task-completion condition rather than polling
        empty(). empty() goes True as soon as the last item is dequeued,
        which is before the worker has finished writing it to the socket.
        """
        pending = self._event_queue
        if pending is None:
            return True
        deadline = None if timeout is None else time.monotonic() + timeout
        with pending.all_tasks_done:
            while pending.unfinished_tasks:
                if deadline is None:
                    pending.all_tasks_done.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                pending.all_tasks_done.wait(remaining)
        return True

    def _flush_at_exit(self) -> None:
        """Last-chance drain of the event queue at interpreter shutdown."""
        if self._event_queue is None or not self._event_queue.unfinished_tasks:
            return
        logger.warning(
            'Exiting with %d ECI event(s) still queued. Call disconnect() '
            'or flush_events() to send them deterministically.',
            self._event_queue.unfinished_tasks,
        )
        if not self._drain_queue(timeout=5.0):
            logger.error(
                'Gave up flushing %d ECI event(s) at exit; they were not '
                'sent.', self._event_queue.unfinished_tasks
            )

    @check_connected
    def flush_events(self, timeout: float = None) -> bool:
        """Block until every queued asynchronous event has been sent.

        Called automatically by ``end_rec()`` and ``disconnect()``, so most
        experiments never need it. Returns True if the queue drained.
        """
        return self._drain_queue(timeout=timeout)

    def pending_events(self) -> int:
        """Return how many asynchronous events are still waiting to be sent.

        Zero in synchronous mode. A value that grows without bound means
        the sender cannot keep up with the event rate.
        """
        if self._event_queue is None:
            return 0
        return self._event_queue.qsize()

    def event_errors(self) -> list:
        """Return any errors raised while sending asynchronous events.

        Asynchronous sends cannot raise into the experiment's own code, so
        failures are collected here instead of being lost. An empty list
        means every queued event was sent successfully.
        """
        with self._clock_lock:
            return list(self._event_errors)

    def _stop_event_sender(self) -> None:
        with self._thread_lock:
            if self._event_thread is None:
                return
            atexit.unregister(self._flush_at_exit)
            self._event_queue.put(self._event_stop)
            self._event_thread.join(timeout=5.0)
            self._event_thread = None
            self._event_queue = None

    def rec_start(self) -> float:
        """Get recording start time from time.time()

        Returns
        -------
        float
            Time of recording start, from ``time.time()``.
        """
        return self._recording_start

    def clock_offsets(self) -> list:
        """Return server clock-start observations from sync_return_clock()."""
        with self._clock_lock:
            return list(self._clock_start_history)

    def drift_history(self) -> list:
        """Return NTP offset observations used for drift correction."""
        with self._clock_lock:
            return list(self._drift_history)

    def drift_estimate(self) -> dict:
        """Return the current linear NTP drift estimate.

        The slope is expressed as seconds of NTP offset change per second of
        local elapsed time. Use ``slope_ms_per_hour()`` to express it as
        milliseconds per hour.

        The model-specific keys (``slope``, ``intercept``, ``model_span``
        and the residuals) are None until a fit has been accepted; every
        other key is always present.
        """
        with self._clock_lock:
            estimate = self._ntp_drift_regression()
            valid_samples = self._valid_drift_samples(windowed=False)
            rejected_samples = len(self._drift_history) - len(valid_samples)
            active = self._drift_active_model

            elapsed = None
            if self._sync_monotonic is not None:
                elapsed = ntp_monotonic_time() - self._sync_monotonic

            if estimate is None:
                predicted = self._predict_active_ntp_offset(elapsed)
                if predicted is None:
                    predicted = getattr(self, '_offset_mono', None)
            else:
                predicted = self._predict_ntp_offset(elapsed)

            # One dict, built once. This used to be two near-identical
            # literals -- one per branch -- sharing about fifteen keys, so
            # a field added to one could silently be missed in the other.
            report = {
                'enabled': self._drift_correction,
                'samples': len(self._drift_history),
                'valid_samples': len(valid_samples),
                'rejected_samples': rejected_samples,
                'min_samples': self._drift_min_samples,
                'min_span': self._drift_min_span,
                'max_delay': self._drift_max_delay,
                'max_residual': self._drift_max_residual,
                'window': self._drift_window,
                'window_minutes': (
                    None if self._drift_window is None
                    else self._drift_window / 60.0
                ),
                'model_cached': not self._drift_model_dirty,
                'predicted_offset': predicted,
                'initial_offset': getattr(self, '_offset_mono', None),
                'elapsed': elapsed,
                'active_slope': (
                    None if active is None else active.get('slope')
                ),
                'active_anchor_elapsed': (
                    None if active is None else active.get('anchor_elapsed')
                ),
                'active_anchor_offset': (
                    None if active is None else active.get('anchor_offset')
                ),
                # Present in both cases so consumers never have to test
                # for the key, only for the value.
                'model_samples': 0,
                'model_span': None,
                'model_updated_local_time': None,
                'model_max_residual': None,
                'model_rms_residual': None,
                'slope': None,
                'intercept': None,
            }
            if estimate is not None:
                report.update({
                    'model_samples': estimate['sample_count'],
                    'model_span': estimate['span'],
                    'model_updated_local_time': estimate[
                        'updated_local_time'
                    ],
                    'model_max_residual': estimate['max_residual'],
                    'model_rms_residual': estimate['rms_residual'],
                    'slope': estimate['slope'],
                    'intercept': estimate['intercept'],
                })
            return report

    @check_connected
    def set_drift_correction(self, enabled: bool = True) -> bool:
        """Enable or disable drift-corrected getTime()."""
        with self._clock_lock:
            self._drift_correction = enabled
            return self._drift_correction

    @check_connected
    def set_drift_requirements(
        self,
        min_samples: int = None,
        min_span: float = None,
    ) -> dict:
        """Set minimum evidence needed before applying drift correction.

        Parameters
        ----------
        min_samples:
            Minimum number of NTP offset observations needed before fitting a
            drift line. Omit to leave the current value unchanged.
        min_span:
            Minimum time window, in seconds, between the first and most recent
            drift samples before the fitted correction is applied. Omit to
            leave the current value unchanged.

        Notes
        -----
        Omitting an argument leaves that setting alone, matching every other
        ``set_*``/``configure_*`` method on this class. This used to default
        to hardcoded values instead, so calling it to change one setting
        silently reset the other.
        """
        if min_samples is not None and min_samples < 2:
            raise ValueError('min_samples must be at least 2')
        if min_span is not None and min_span < 0:
            raise ValueError('min_span must be non-negative')
        with self._clock_lock:
            if min_samples is not None:
                self._drift_min_samples = min_samples
            if min_span is not None:
                self._drift_min_span = min_span
            self._drift_model_dirty = True
            return {
                'min_samples': self._drift_min_samples,
                'min_span': self._drift_min_span,
            }

    @check_connected
    def set_drift_model_options(
        self,
        max_delay: float = None,
        max_residual: float = None,
        window_minutes: float = None,
    ) -> dict:
        """Set quality and rolling-window options for drift modeling.

        Parameters
        ----------
        max_delay:
            Maximum NTP round-trip delay, in seconds. Samples above this delay
            remain in ``drift_history()`` for auditing, but are excluded from
            the fitted correction.
        max_residual:
            Maximum absolute residual, in seconds, allowed for an accepted
            drift line. Fitted lines above this threshold are rejected and the
            last active correction continues.
        window_minutes:
            Number of recent minutes to use for the fitted model. Use 0 to fit
            all valid samples. Older valid samples remain in
            ``drift_history()`` but do not affect prediction when a rolling
            window is enabled.
        """
        with self._clock_lock:
            if max_delay is not None:
                if max_delay <= 0:
                    raise ValueError('max_delay must be positive')
                self._drift_max_delay = max_delay
            if max_residual is not None:
                if max_residual <= 0:
                    raise ValueError('max_residual must be positive')
                self._drift_max_residual = max_residual
            if window_minutes is not None:
                if window_minutes < 0:
                    raise ValueError('window_minutes must be non-negative')
                self._drift_window = (
                    None if window_minutes == 0
                    else window_minutes * 60.0
                )
            self._drift_model_dirty = True
            return {
                'max_delay': self._drift_max_delay,
                'max_residual': self._drift_max_residual,
                'window': self._drift_window,
                'window_minutes': (
                    None if self._drift_window is None
                    else self._drift_window / 60.0
                ),
            }

    @check_connected
    def set_drift_sampling(
        self,
        samples: int = None,
        spacing: float = None,
    ) -> dict:
        """Set the default NTP burst size used by ``sample_drift()``.

        Parameters
        ----------
        samples:
            Number of NTP queries per drift sample. The lowest-delay reply
            is kept. Values of 4 to 8 substantially reduce offset noise for
            a cost of a few tens of milliseconds per sample.
        spacing:
            Seconds between queries inside one burst.
        """
        with self._clock_lock:
            if samples is not None:
                if samples < 1:
                    raise ValueError('samples must be at least 1')
                self._drift_samples_per_call = samples
            if spacing is not None:
                if spacing < 0:
                    raise ValueError('spacing must be non-negative')
                self._drift_sample_spacing = spacing
            return {
                'samples': self._drift_samples_per_call,
                'spacing': self._drift_sample_spacing,
            }

    @check_connected
    def configure_auto_drift(
        self,
        enabled: bool = None,
        interval: float = None,
        min_pause: float = None,
        background: bool = None,
    ) -> dict:
        """Configure automatic NTP drift sampling.

        Every argument is optional and omitting one leaves that setting
        unchanged, so it is safe to retune a single knob mid-session.

        Parameters
        ----------
        enabled:
            Whether drift samples may be taken at all. On by default for a
            new connection; omit to leave the current value unchanged.
            When False, ``sample_drift_if_due()`` is a no-op and no model
            is ever fitted; an explicit ``sample_drift()`` still works.
        interval:
            Target seconds between drift samples.
        min_pause:
            Minimum idle time, in seconds, an experiment must be able to
            offer before a sample is taken. Only used by
            ``sample_drift_if_due()``.
        background:
            Who does the sampling.

            True (default) starts a background thread that samples on the
            interval with no cooperation required. The NTP query runs
            outside every lock, so it cannot block ``getTime()`` by more
            than a few microseconds. Its network wakeup can in principle
            land near a screen flip, but this has not been observed to
            matter in practice across repeated hour-long validation runs
            -- background sampling has matched or beaten cooperative
            sampling on every measure tried, and it has no failure mode
            equivalent to cooperative sampling's silent starvation.

            False is for advanced use: the experiment calls
            ``sample_drift_if_due()`` itself from a point it knows is
            safe, giving it explicit control over exactly when each NTP
            query happens -- for example to guarantee one never coincides
            with a specific critical window, or to force a sample at a
            chosen moment via a manual ``sample_drift()`` call instead.
            The package owns the schedule, the experiment owns the
            timing-safety window. **Nothing samples on its own: if
            sample_drift_if_due() is never called, no samples are
            collected and drift correction never engages.**
        """
        with self._clock_lock:
            if enabled is not None:
                self._auto_drift_enabled = enabled
            if interval is not None:
                if interval <= 0:
                    raise ValueError('interval must be positive')
                self._auto_drift_interval = interval
            if min_pause is not None:
                if min_pause < 0:
                    raise ValueError('min_pause must be non-negative')
                self._auto_drift_min_pause = min_pause
            if background is not None:
                self._auto_drift_background = background
        # Started outside the lock: the thread takes _clock_lock itself.
        if self._auto_drift_enabled and self._auto_drift_background:
            self._start_auto_drift_thread()
        else:
            self._stop_auto_drift_thread()
        with self._clock_lock:
            return {
                'enabled': self._auto_drift_enabled,
                'interval': self._auto_drift_interval,
                'min_pause': self._auto_drift_min_pause,
                'background': self._auto_drift_background,
            }

    def _warn_if_undersampled(self) -> None:
        """Warn when auto-drift was enabled but nothing ever sampled.

        configure_auto_drift() only records a schedule. With background
        sampling off, sample_drift_if_due() is the only thing that acts on
        it, so an experiment that enables auto-drift and never calls it
        collects nothing and silently loses drift correction. This is the
        one place that can notice.
        """
        with self._clock_lock:
            if not self._auto_drift_enabled or self._auto_drift_background:
                return
            if self._sync_monotonic is None:
                return
            elapsed = ntp_monotonic_time() - self._sync_monotonic
            expected = elapsed / self._auto_drift_interval
            actual = len(self._drift_history)
            if expected < 4 or actual >= expected * 0.25:
                return
        logger.warning(
            'Auto-drift was enabled but only %d NTP sample(s) were '
            'collected in %.0f s, where the %.0f s interval implies about '
            '%.0f. sample_drift_if_due() is what actually takes a sample; '
            'call it from an inter-trial interval, or use '
            'configure_auto_drift(background=True).',
            actual, elapsed, self._auto_drift_interval, expected,
        )
        self._append_error_log({
            'record': 'drift_undersampled',
            'samples_collected': actual,
            'samples_expected': expected,
            'elapsed': elapsed,
            'interval': self._auto_drift_interval,
            'clock': None,
        })

    def _auto_drift_join_timeout(self) -> float:
        """How long a sampler could legitimately still be working.

        A stop request can arrive mid-burst, and a burst against an
        unresponsive server takes one timeout per query plus the spacing
        between them. Joining for less than that abandons a live thread.
        """
        samples = max(1, self._drift_samples_per_call)
        return (
            samples * self._ntp_timeout
            + (samples - 1) * self._drift_sample_spacing
            + 1.0
        )

    def _start_auto_drift_thread(self) -> None:
        with self._thread_lock:
            existing = self._auto_drift_thread
            if existing is not None and existing.is_alive():
                return
            # Each thread gets its own stop Event. Sharing one lets a new
            # thread clear the flag before an abandoned older thread has
            # noticed it was told to stop, reviving it as a duplicate.
            stop = threading.Event()
            thread = threading.Thread(
                target=self._auto_drift_loop,
                args=(stop,),
                name='eci-drift-sampler',
                daemon=True,
            )
            self._auto_drift_stop = stop
            self._auto_drift_thread = thread
            thread.start()

    def _stop_auto_drift_thread(self) -> None:
        with self._thread_lock:
            thread = self._auto_drift_thread
            if thread is None:
                return
            self._auto_drift_stop.set()
            thread.join(timeout=self._auto_drift_join_timeout())
            if thread.is_alive():
                # Keep the handle. Dropping it would let a replacement
                # start alongside a sampler that is still running and
                # still mutating the drift history.
                logger.error(
                    'Background NTP drift sampler did not stop within '
                    '%.1f s and is still running; not starting a '
                    'replacement until it exits.',
                    self._auto_drift_join_timeout(),
                )
                return
            self._auto_drift_thread = None

    def _auto_drift_loop(self, stop: threading.Event) -> None:
        while not stop.wait(self._auto_drift_interval):
            with self._clock_lock:
                active = (
                    self._auto_drift_enabled and self._auto_drift_background
                )
            if not active:
                continue
            if not self._connected or self._syncepoch is None:
                continue
            try:
                self.sample_drift()
            except Exception as err:
                logger.error(
                    'Background NTP drift sample failed: %s: %s',
                    type(err).__name__, err,
                )

    @check_connected
    def set_drift_stability(
        self,
        slew: float = None,
        max_model_age: float = None,
        stall_after: int = None,
    ) -> dict:
        """Tune how the corrector tracks the measured NTP offset level.

        Parameters
        ----------
        slew:
            Maximum rate, in seconds of correction per second of elapsed
            time, at which an outstanding level error is retired. This
            bounds how fast the applied correction may move, so a new fit
            never steps event timestamps. Use 0 to apply level corrections
            instantly.
        max_model_age:
            Maximum seconds a fitted slope may be extrapolated after its
            anchor. Past this age the correction holds its last value
            instead of extrapolating an increasingly stale rate. Use 0 for
            unbounded extrapolation.
        stall_after:
            Number of consecutive rejected fits, after the model has once
            engaged, before a ``drift_model_stalled`` record is logged. A
            stalled model keeps extrapolating its last accepted slope, so
            this is the only warning that timing error is accumulating.
        """
        with self._clock_lock:
            if slew is not None:
                if slew < 0:
                    raise ValueError('slew must be non-negative')
                self._drift_slew = slew
                if self._drift_active_model is not None:
                    self._drift_active_model['slew'] = slew
            if max_model_age is not None:
                if max_model_age < 0:
                    raise ValueError('max_model_age must be non-negative')
                self._drift_max_model_age = (
                    None if max_model_age == 0 else max_model_age
                )
            if stall_after is not None:
                if stall_after < 1:
                    raise ValueError('stall_after must be at least 1')
                self._drift_stall_after = stall_after
            return {
                'slew': self._drift_slew,
                'max_model_age': self._drift_max_model_age,
                'stall_after': self._drift_stall_after,
            }

    @check_connected
    def refresh_drift_model(self) -> dict:
        """Force the NTP drift model to refit from current samples.

        This does not query NTP and does not send any ECI clock-sync command.
        It only invalidates the cached fit, recomputes it from the current
        valid/windowed drift samples, and activates the accepted model
        continuously at the current elapsed time. If the refit fails the
        current active correction, if any, continues.
        """
        with self._clock_lock:
            self._drift_model_dirty = True
            elapsed = None
            if self._sync_monotonic is not None:
                elapsed = ntp_monotonic_time() - self._sync_monotonic
            estimate = self._ntp_drift_regression(
                activation_elapsed=elapsed
            )
            report = self.drift_estimate()
        self._flush_pending_log_records()
        return report

    def _query_ntp_best_of(self, samples: int, spacing: float):
        """Query NTP several times quickly and keep the lowest-delay reply.

        NTP offset error is dominated by path asymmetry, and asymmetry
        tracks round-trip delay: the fastest reply in a short burst is the
        most trustworthy one. Selecting the minimum-delay reply is far more
        effective than averaging the burst, because averaging folds the bad
        replies back in. Reducing per-sample offset noise this way directly
        reduces the slope noise of the regression, which is the dominant
        error source in the correction.
        """
        client = NTPClient()
        best = None
        burst = []
        for index in range(max(1, samples)):
            if index and spacing > 0:
                time.sleep(spacing)
            try:
                response = client.request(
                    self._ntp_ip, version=3, timeout=self._ntp_timeout,
                )
            except Exception as err:
                burst.append({'error': f'{type(err).__name__}: {err}'})
                continue
            burst.append({
                'offset': response.offset,
                'delay': response.delay,
            })
            if best is None or response.delay < best.delay:
                best = response
        if best is None:
            raise RuntimeError(
                'all NTP queries in the drift sample burst failed'
            )
        return best, burst

    def _note_ntp_sample_failure(self, err: Exception) -> None:
        """Record a burst in which every NTP query failed."""
        with self._clock_lock:
            now = time.monotonic()
            self._ntp_failure_count += 1
            self._ntp_consecutive_failures += 1
            self._ntp_last_attempt_monotonic = now
            self._ntp_last_error = f'{type(err).__name__}: {err}'
            consecutive = self._ntp_consecutive_failures
            # Log on entry to an outage, not once per interval: a long
            # outage would otherwise write a record every 15 seconds.
            report = (
                not self._ntp_failure_reported
                and consecutive >= self._ntp_failures_before_report
            )
            if report:
                self._ntp_failure_reported = True
                context = self._drift_transition_context()
                context.update(
                    record='drift_sampling_failed',
                    consecutive_failures=consecutive,
                    total_failures=self._ntp_failure_count,
                    error=self._ntp_last_error,
                    clock=None,
                )
        if report:
            logger.warning(
                'NTP drift sampling has failed %d times in a row (%s). '
                'No new samples means no new fits, so the applied '
                'correction is frozen at its last value and timing error '
                'will grow.',
                consecutive, self._ntp_last_error,
            )
            self._queue_log_record(context)

    def _note_ntp_sample_success(self) -> None:
        """Record a burst that produced a usable reply."""
        with self._clock_lock:
            now = time.monotonic()
            self._ntp_last_attempt_monotonic = now
            self._ntp_last_success_monotonic = now
            failures = self._ntp_consecutive_failures
            self._ntp_consecutive_failures = 0
            recovered = self._ntp_failure_reported
            if recovered:
                self._ntp_failure_reported = False
                context = self._drift_transition_context()
                context.update(
                    record='drift_sampling_recovered',
                    failures_during_outage=failures,
                    clock=None,
                )
        if recovered:
            logger.info(
                'NTP drift sampling recovered after %d failed bursts.',
                failures,
            )
            self._queue_log_record(context)

    def _ntp_sampling_health(self) -> dict:
        """Whether drift samples are still actually arriving.

        Separate from the stall detector, which counts *rejected* fits: a
        total sampling outage produces no fits at all, so it is silent to
        every other health signal in the package.
        """
        with self._clock_lock:
            expected = self._auto_drift_enabled and self._auto_drift_background
            last_success = self._ntp_last_success_monotonic
            interval = self._auto_drift_interval
            max_age = self._drift_max_model_age
            failures = self._ntp_failure_count
            consecutive = self._ntp_consecutive_failures
            last_error = self._ntp_last_error
            synced = self._syncepoch is not None
        age = (
            None if last_success is None
            else time.monotonic() - last_success
        )
        # Generous: two intervals covers an ordinary hiccup, and there is
        # no point complaining sooner than extrapolation is bounded at.
        threshold = max(2.0 * interval, max_age) if max_age else 2.0 * interval
        if not (expected and synced):
            stale = False
        elif last_success is None:
            # Sampling was expected but nothing has ever succeeded.
            stale = failures > 0
        else:
            stale = age > threshold
        return {
            'ntp_sampling_expected': expected,
            'ntp_sample_failures': failures,
            'ntp_consecutive_failures': consecutive,
            'ntp_seconds_since_success': age,
            'ntp_sampling_stale': stale,
            'ntp_staleness_threshold': threshold,
            'ntp_last_error': last_error,
        }

    @check_connected
    def sample_drift(
        self,
        samples: int = None,
        spacing: float = None,
    ) -> dict:
        """Query the NTP server and record an offset sample.

        This does not send any ECI clock-sync command. It only asks the
        amplifier/Net Station NTP server for the current NTP offset so that
        client-side timestamps can be drift-corrected.

        Parameters
        ----------
        samples:
            Number of NTP queries to make in this burst. The lowest-delay
            reply becomes the recorded sample; the rest are summarized in
            the returned record but do not enter the drift history.
            Defaults to ``set_drift_sampling(samples=...)``.
        spacing:
            Seconds to wait between queries within the burst. Defaults to
            ``set_drift_sampling(spacing=...)``.

        Notes
        -----
        A burst blocks for roughly ``(samples - 1) * spacing`` plus the
        round trips. Call this between trials, never near a screen flip.
        """
        if not self._ntp_ip:
            raise NetStationNoNTPIP()
        if self._syncepoch is None:
            raise RuntimeError('sample_drift is unavailable before NTP sync')
        if samples is None:
            samples = self._drift_samples_per_call
        if spacing is None:
            spacing = self._drift_sample_spacing
        # The network round trips happen outside every lock.
        try:
            response, burst = self._query_ntp_best_of(samples, spacing)
        except Exception as err:
            # A burst where every query failed produces no sample, so no
            # fit is attempted and the stall detector never sees anything.
            # Record it explicitly instead.
            self._note_ntp_sample_failure(err)
            self._flush_pending_log_records()
            raise
        self._note_ntp_sample_success()
        with self._clock_lock:
            self._auto_drift_last_monotonic = time.monotonic()
            sample = self._record_ntp_drift_sample(
                response,
                source='drift_sample',
                burst=burst,
            )
        # Outside the lock: the refit above may have noticed a model
        # transition, and writing that record is filesystem I/O.
        self._flush_pending_log_records()
        return sample

    @check_connected
    def sample_drift_if_due(self, available_pause: float = None) -> dict:
        """Take a drift sample only if one is due and there is time for it.

        The package owns the sampling schedule; the experiment owns the
        timing-safety window. Call this from an inter-trial interval and
        pass how much idle time is available.

        Parameters
        ----------
        available_pause:
            Seconds of idle time the experiment can safely give up. When
            this is shorter than the configured minimum pause, no sample is
            taken and the sampler waits for a later opportunity.
        """
        with self._clock_lock:
            if not self._auto_drift_enabled:
                return {'sampled': False, 'reason': 'disabled'}
            if self._syncepoch is None:
                return {'sampled': False, 'reason': 'not_synced'}
            now = time.monotonic()
            last = self._auto_drift_last_monotonic
            if last is not None:
                due_in = self._auto_drift_interval - (now - last)
                if due_in > 0:
                    return {
                        'sampled': False,
                        'reason': 'not_due',
                        'seconds_until_due': due_in,
                    }
            min_pause = self._auto_drift_min_pause
        if available_pause is not None and available_pause < min_pause:
            return {
                'sampled': False,
                'reason': 'pause_too_short',
                'min_pause': min_pause,
            }
        return {
            'sampled': True,
            'reason': 'due',
            'sample': self.sample_drift(),
        }

    def session_summary(self) -> dict:
        """One-call, human-checkable report of how the session's clock behaved.

        Pulls together the handful of things worth checking after a run
        -- whether drift correction ever engaged, whether it is currently
        stalled, and whether any asynchronous event sends failed -- so
        that checking a session's health does not require opening the
        JSON-lines error log by hand or combining ``clock_state()`` and
        ``event_errors()`` yourself. Safe to call at any point, including
        mid-session; ``ok`` reflects the state as of the call.
        """
        state = self.clock_state()
        errors = self.event_errors()
        with self._clock_lock:
            eci_failures = self._eci_error_count
        health = self._ntp_sampling_health()
        engaged = bool(state.get('drift_accepted_fits'))
        stalled = bool(state.get('drift_stalled'))
        slope = state.get('active_drift_slope')
        return {
            'ok': (
                engaged and not stalled and not errors and not eci_failures
                # A sampling outage produces no rejected fits, so `stalled`
                # stays False while the correction quietly goes stale.
                and not health['ntp_sampling_stale']
            ),
            'drift_engaged': engaged,
            'drift_stalled': stalled,
            'drift_accepted_fits': state.get('drift_accepted_fits'),
            'drift_rejected_fits': state.get('drift_rejected_fits'),
            'drift_samples': state.get('drift_samples'),
            'active_drift_slope_ms_per_hour': (
                slope_ms_per_hour(slope)
            ),
            'event_send_failures': len(errors),
            'eci_response_failures': eci_failures,
            'ntp_sampling_stale': health['ntp_sampling_stale'],
            'ntp_sample_failures': health['ntp_sample_failures'],
            'ntp_seconds_since_success': health['ntp_seconds_since_success'],
        }

    def drift_settings(self) -> dict:
        """Report every drift setting currently in effect.

        This is a *report*, not a template to copy and edit. It answers
        "what did this session actually run with", which is worth logging
        beside your data, and "what are the defaults", which is otherwise
        surprisingly hard to obtain: every drift parameter of
        :meth:`connect` defaults to None meaning *leave unchanged*, so
        reading the signature tells you nothing about the real values.

        Deliberately usable before ``connect()``, so a launcher dialog can
        populate its fields with the true defaults.

        Notes
        -----
        Keys are the :meth:`connect` keyword names, matching the settings
        table in the drift documentation.

        Do not save the whole dictionary as a configuration template.
        Doing so pins all of these values at the version you captured
        them, so a later release that improves a default cannot reach your
        experiment, and nothing reports that it did not. To set options
        programmatically, build a dict of only the ones you are choosing
        deliberately and pass it as ``ns.connect(ntp_ip=..., **options)``;
        everything you leave out then tracks the package default.

        ``drift_stall_after`` is included because it is a real setting and
        this is a complete report, but it is not a ``connect()`` argument
        -- it is set afterwards through :meth:`set_drift_stability`. That
        also means splatting this dictionary straight into ``connect()``
        raises TypeError rather than quietly doing the wrong thing, which
        is the intended outcome given the paragraph above.

        ``drift_window_minutes`` and ``drift_max_model_age`` report ``0``
        for "no limit", matching the convention ``connect()`` accepts,
        rather than the None they are stored as internally.
        """
        with self._clock_lock:
            return {
                'drift_correction': self._drift_correction,
                'drift_min_samples': self._drift_min_samples,
                'drift_min_span': self._drift_min_span,
                'drift_max_delay': self._drift_max_delay,
                'drift_max_residual': self._drift_max_residual,
                'drift_window_minutes': (
                    0.0 if self._drift_window is None
                    else self._drift_window / 60.0
                ),
                'drift_samples': self._drift_samples_per_call,
                'drift_sample_spacing': self._drift_sample_spacing,
                'drift_slew': self._drift_slew,
                'drift_max_model_age': (
                    0.0 if self._drift_max_model_age is None
                    else self._drift_max_model_age
                ),
                'drift_stall_after': self._drift_stall_after,
                'auto_drift': self._auto_drift_enabled,
                'auto_drift_interval': self._auto_drift_interval,
                'auto_drift_min_pause': self._auto_drift_min_pause,
                'auto_drift_background': self._auto_drift_background,
            }

    @staticmethod
    def clock_report() -> dict:
        """Describe the clocks used by the vendored NTP implementation."""
        report = ntp_clock_report()
        report['coarse_monotonic_platform'] = _COARSE_MONOTONIC
        return report

    def _check_clock_resolution(self) -> dict:
        """Log the clock report, and warn loudly if the clock is too coarse."""
        report = check_ntp_clock_resolution()
        report['coarse_monotonic_platform'] = _COARSE_MONOTONIC
        return report

    def clock_state(self) -> dict:
        """Return current client/server clock synchronization state."""
        health = self._ntp_sampling_health()
        with self._clock_lock:
            drift = self.drift_estimate()
            return {
                **health,
                'client_clock_start_ntp': self._client_clock_start_ntp,
                'server_clock_start_ntp': self._server_clock_start_ntp,
                'syncepoch': self._syncepoch,
                'sync_monotonic': self._sync_monotonic,
                'ntp_offset': getattr(self, '_offset_mono', None),
                'ntp_offset_raw': getattr(self, '_offset', None),
                'sys_mono_skew': (
                    self._drift_history[-1]['sys_mono_skew']
                    if self._drift_history else None
                ),
                'drift_correction': self._drift_correction,
                'drift_samples': len(self._drift_history),
                'drift_min_samples': self._drift_min_samples,
                'drift_min_span': self._drift_min_span,
                'drift_max_delay': self._drift_max_delay,
                'drift_max_residual': self._drift_max_residual,
                'drift_window': self._drift_window,
                'drift_valid_samples': drift.get('valid_samples'),
                'drift_rejected_samples': drift.get('rejected_samples'),
                'drift_model_samples': drift.get('model_samples'),
                'drift_model_span': drift.get('model_span'),
                'drift_model_max_residual': drift.get('model_max_residual'),
                'drift_model_rms_residual': drift.get('model_rms_residual'),
                'drift_slope': drift.get('slope'),
                'active_drift_slope': drift.get('active_slope'),
                'predicted_ntp_offset': drift.get('predicted_offset'),
                'drift_accepted_fits': self._drift_accepted_fits,
                'drift_rejected_fits': self._drift_rejected_fits,
                'drift_consecutive_rejections':
                    self._drift_consecutive_rejections,
                'drift_stalled': self._drift_stalled,
                'drift_last_reject_reason': self._drift_last_reject_reason,
                'drift_slew': self._drift_slew,
                'drift_max_model_age': self._drift_max_model_age,
                'drift_samples_per_call': self._drift_samples_per_call,
                'drift_pending_error': (
                    None if self._drift_active_model is None
                    else self._drift_active_model.get('error')
                ),
                'drift_model_age': (
                    None
                    if self._drift_active_model is None
                    or self._sync_monotonic is None
                    else (
                        ntp_monotonic_time() - self._sync_monotonic
                        - self._drift_active_model['anchor_elapsed']
                    )
                ),
            }

    def _record_ntp_drift_sample(
        self,
        response,
        source: str,
        local_time: float = None,
        monotonic_time: float = None,
        burst: list = None,
    ) -> dict:
        # As in ntpsync(): the vendored client always attaches these, and
        # silently substituting a differently-framed reading would corrupt
        # the drift fit rather than fail visibly.
        if local_time is None:
            local_time = response.local_time
        if monotonic_time is None:
            monotonic_time = response.monotonic_time
        elapsed = None
        if self._sync_monotonic is not None:
            elapsed = monotonic_time - self._sync_monotonic
        sample = {
            'source': source,
            'local_time': local_time,
            'monotonic_time': monotonic_time,
            'elapsed': elapsed,
            'offset': response.offset,
            'delay': response.delay,
            'tx_time': response.tx_time,
        }
        # NTP reports the offset against the local *system* clock, but
        # event timestamps are built on the *monotonic* clock. Those two
        # frames diverge whenever the OS time daemon disciplines the system
        # clock, and on macOS that happens continuously. Re-reference the
        # offset to the monotonic clock so OS clock adjustments cancel out
        # instead of being injected into event timestamps.
        sample['sys_mono_skew'] = local_time - monotonic_time
        sample['offset_mono'] = response.offset + sample['sys_mono_skew']
        delays = [
            entry['delay'] for entry in (burst or [])
            if 'delay' in entry
        ]
        sample['burst_size'] = len(burst) if burst else 1
        sample['burst_ok'] = len(delays) if burst else 1
        sample['burst_worst_delay'] = max(delays) if delays else None
        sample['valid'] = response.delay <= self._drift_max_delay
        sample['reject_reason'] = None if sample['valid'] else 'high_delay'
        self._drift_history.append(sample)
        self._drift_model_dirty = True
        if self._debug:
            elapsed_text = 'None' if elapsed is None else f'{elapsed:.6f}'
            valid_text = 'valid' if sample['valid'] else 'rejected=high_delay'
            print(
                f'{cyan}NTP drift sample source={source} '
                f'elapsed={elapsed_text} offset={response.offset:.9f} '
                f'delay={response.delay:.9f} {valid_text}{reset}'
            )
        # Refit now, on whichever thread took the sample, rather than
        # leaving the model dirty for the next reader to rebuild. getTime()
        # is a reader, and it is called from screen-flip callbacks -- it
        # must never be the thread that pays for an O(window) regression.
        self._ntp_drift_regression()
        return dict(sample)

    def _ntp_drift_regression(self, activation_elapsed: float = None):
        if not self._drift_model_dirty:
            return self._drift_model

        self._drift_model = self._fit_ntp_drift_regression()
        # Clear the dirty flag before noting the transition. The transition
        # record reads drift state, and any path back into this method
        # would otherwise refit and recurse without end.
        self._drift_model_dirty = False
        activated = False
        if self._drift_model is not None:
            if activation_elapsed is None and self._drift_history:
                activation_elapsed = self._drift_history[-1].get('elapsed')
            activated = self._activate_drift_model(
                self._drift_model, activation_elapsed
            )
        self._note_drift_transition(self._drift_model, activated=activated)
        return self._drift_model

    def _drift_transition_context(self) -> dict:
        """Drift fields for a transition record.

        Reads attributes directly rather than calling clock_state(), which
        would re-enter the regression path this is called from.
        """
        active = self._drift_active_model
        last = self._drift_history[-1] if self._drift_history else {}
        # Anchor the record to the sample that triggered the refit rather
        # than to a fresh clock read. The transition is a statement about
        # the fit, and the fit is made of samples.
        elapsed = last.get('elapsed')
        if elapsed is None and self._sync_monotonic is not None:
            elapsed = ntp_monotonic_time() - self._sync_monotonic
        model_age = None
        if active is not None and elapsed is not None:
            model_age = elapsed - active['anchor_elapsed']
        return {
            'elapsed': elapsed,
            'drift_samples': len(self._drift_history),
            'drift_accepted_fits': self._drift_accepted_fits,
            'drift_rejected_fits': self._drift_rejected_fits,
            'drift_consecutive_rejections': self._drift_consecutive_rejections,
            'drift_last_reject_reason': self._drift_last_reject_reason,
            'active_slope': None if active is None else active['slope'],
            'active_slope_ms_per_hour': (
                None if active is None
                else slope_ms_per_hour(active['slope'])
            ),
            'drift_model_age': model_age,
            'extrapolation_frozen': (
                model_age is not None
                and self._drift_max_model_age is not None
                and model_age > self._drift_max_model_age
            ),
            'sys_mono_skew': last.get('sys_mono_skew'),
            'last_sample_delay': last.get('delay'),
        }

    def _note_drift_transition(self, estimate, activated=False) -> None:
        """Log when the drift model engages, stalls, or recovers.

        A stalled model is silent by construction: fits are refused, the
        last accepted slope keeps being extrapolated, and nothing else goes
        wrong until the timing error has grown. These records make that
        visible while it is happening rather than afterwards.
        """
        if estimate is None:
            self._drift_consecutive_rejections += 1
            # Startup rejections are expected, not a stall.
            if self._drift_accepted_fits == 0 or self._drift_stalled:
                return
            if self._drift_consecutive_rejections < self._drift_stall_after:
                return
            self._drift_stalled = True
            context = self._drift_transition_context()
            self._drift_stall_started_elapsed = context['elapsed']
            logger.warning(
                'NTP drift model stalled: %d consecutive rejected fits '
                '(%s). The last accepted slope is still being extrapolated; '
                'timing error will grow until a fit is accepted.',
                self._drift_consecutive_rejections,
                self._drift_last_reject_reason,
            )
            self._queue_log_record(dict(
                context, record='drift_model_stalled', clock=None,
            ))
            return

        first_fit = activated and self._drift_accepted_fits == 1
        was_stalled = self._drift_stalled
        rejections = self._drift_consecutive_rejections
        self._drift_consecutive_rejections = 0
        self._drift_stalled = False

        if first_fit:
            context = self._drift_transition_context()
            context['active_slope'] = estimate['slope']
            context['active_slope_ms_per_hour'] = slope_ms_per_hour(
                estimate['slope'])
            logger.info(
                'NTP drift correction engaged: slope %.2f ms/hour from %d '
                'samples over %.0f s.',
                slope_ms_per_hour(estimate['slope']),
                estimate['sample_count'],
                estimate['span'],
            )
            self._queue_log_record(dict(
                context,
                record='drift_model_engaged',
                model_samples=estimate['sample_count'],
                model_span=estimate['span'],
                clock=None,
            ))
            return

        if was_stalled:
            context = self._drift_transition_context()
            started = self._drift_stall_started_elapsed
            duration = (
                None if started is None or context['elapsed'] is None
                else context['elapsed'] - started
            )
            self._drift_stall_started_elapsed = None
            logger.warning(
                'NTP drift model recovered after %s s and %d rejected fits.',
                'unknown' if duration is None else f'{duration:.0f}',
                rejections,
            )
            self._queue_log_record(dict(
                context,
                record='drift_model_recovered',
                stall_duration=duration,
                rejected_during_stall=rejections,
                new_slope_ms_per_hour=slope_ms_per_hour(estimate['slope']),
                clock=None,
            ))

    def _reject_fit(self, reason: str):
        self._drift_last_reject_reason = reason
        self._drift_rejected_fits += 1
        if self._debug:
            print(f'{cyan}NTP drift fit rejected: {reason}{reset}')
        return None

    def _fit_ntp_drift_regression(self):
        samples = self._valid_drift_samples(windowed=True)
        if len(samples) < self._drift_min_samples:
            return self._reject_fit('too_few_samples')

        span = samples[-1]['elapsed'] - samples[0]['elapsed']
        if span < self._drift_min_span:
            return self._reject_fit('short_span')

        xs = [sample['elapsed'] for sample in samples]
        ys = [sample['offset_mono'] for sample in samples]
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        denom = sum((x - mean_x) ** 2 for x in xs)
        if denom == 0:
            return self._reject_fit('degenerate_span')
        slope = sum((x - mean_x) * (y - mean_y)
                    for x, y in zip(xs, ys)) / denom
        intercept = mean_y - slope * mean_x
        residuals = [
            y - (intercept + slope * x)
            for x, y in zip(xs, ys)
        ]
        max_residual = max(abs(value) for value in residuals)
        if max_residual > self._drift_max_residual:
            return self._reject_fit('high_residual')
        self._drift_last_reject_reason = None
        rms_residual = (
            sum(value ** 2 for value in residuals) / len(residuals)
        ) ** 0.5
        return {
            'fit_id': (
                len(samples),
                samples[0]['elapsed'],
                samples[-1]['elapsed'],
                slope,
                intercept,
            ),
            'slope': slope,
            'intercept': intercept,
            'sample_count': len(samples),
            'span': span,
            'max_residual': max_residual,
            'rms_residual': rms_residual,
            'first_elapsed': samples[0]['elapsed'],
            'last_elapsed': samples[-1]['elapsed'],
            'updated_local_time': time.time(),
        }

    @staticmethod
    def _is_usable_sample(sample: dict) -> bool:
        return (
            sample.get('elapsed') is not None
            and sample.get('valid', True)
        )

    def _valid_drift_samples(self, windowed: bool = True) -> list:
        """Samples the model may fit, most recent last.

        The full history is kept for auditing, so the windowed case walks
        backward from the newest sample and stops at the window edge
        rather than filtering everything ever recorded. Samples are
        appended in elapsed-time order, which is what makes that safe.
        """
        history = self._drift_history
        if not windowed or self._drift_window is None:
            return [s for s in history if self._is_usable_sample(s)]

        latest_elapsed = None
        window = []
        for sample in reversed(history):
            if not self._is_usable_sample(sample):
                continue
            if latest_elapsed is None:
                latest_elapsed = sample['elapsed']
            if sample['elapsed'] < latest_elapsed - self._drift_window:
                break
            window.append(sample)
        window.reverse()
        return window

    def _predict_ntp_offset(self, elapsed: float = None):
        if elapsed is None:
            if self._sync_monotonic is None:
                return getattr(self, '_offset_mono', None)
            elapsed = ntp_monotonic_time() - self._sync_monotonic

        estimate = self._ntp_drift_regression(activation_elapsed=elapsed)
        if estimate is None:
            active_offset = self._predict_active_ntp_offset(elapsed)
            if active_offset is not None:
                return active_offset
            return getattr(self, '_offset_mono', None)
        return self._predict_active_ntp_offset(elapsed)

    def _drift_model_id(self, estimate: dict):
        # Identifies one accepted fit, so a model is activated exactly once
        # per refit rather than once per getTime() call.
        return estimate['fit_id']

    def _predict_active_ntp_offset(self, elapsed: float):
        """Predict the NTP offset from the active model.

        The prediction is the continuity anchor, plus the modeled slope over
        the elapsed time since the anchor, plus whatever portion of the
        outstanding level error has been retired so far. The last term is
        what closes the loop: it pulls the applied correction toward the
        level the regression actually measured, instead of letting the
        correction free-run on integrated slope alone.
        """
        if elapsed is None:
            return getattr(self, '_offset_mono', None)
        active = self._drift_active_model
        if active is None:
            return getattr(self, '_offset_mono', None)

        dt = elapsed - active['anchor_elapsed']
        if dt < 0:
            dt = 0.0
        # Do not extrapolate a stale slope without bound. Past the age
        # limit, hold the last modeled value rather than letting an
        # increasingly old rate estimate run away.
        max_age = self._drift_max_model_age
        slope_dt = dt if max_age is None else min(dt, max_age)

        error = active.get('error', 0.0)
        slew = active.get('slew', 0.0)
        if slew <= 0 or error == 0.0:
            retired = error
        elif error > 0:
            retired = min(error, slew * dt)
        else:
            retired = max(error, -slew * dt)

        return (
            active['anchor_offset'] +
            active['slope'] * slope_dt +
            retired
        )

    def _activate_drift_model(self, estimate: dict, elapsed: float) -> bool:
        model_id = self._drift_model_id(estimate)
        active = self._drift_active_model
        if active is not None and active.get('model_id') == model_id:
            return False
        if elapsed is None:
            return False

        # Continuity anchor: whatever the previous model predicts right now.
        # Activating a new fit never steps event timestamps.
        anchor_offset = self._predict_active_ntp_offset(elapsed)
        if anchor_offset is None:
            anchor_offset = getattr(self, '_offset_mono', None)

        # Level the new regression actually measures at this instant. The
        # old code computed this, stored it for diagnostics, and then threw
        # it away -- which made the correction an open-loop integral of
        # noisy slope estimates, free to random-walk without bound. Keeping
        # it as an error term to retire is what makes the corrector stable
        # over long recordings.
        target_offset = estimate['intercept'] + estimate['slope'] * elapsed
        if anchor_offset is None:
            anchor_offset = target_offset

        self._drift_active_model = {
            'model_id': model_id,
            'slope': estimate['slope'],
            'anchor_elapsed': elapsed,
            'anchor_offset': anchor_offset,
            'raw_anchor_offset': target_offset,
            'error': target_offset - anchor_offset,
            'slew': self._drift_slew,
            'activated_local_time': time.time(),
        }
        self._drift_accepted_fits += 1
        if self._debug:
            error_ms = (target_offset - anchor_offset) * 1000.0
            print(
                f'{cyan}NTP drift model activated '
                f'elapsed={elapsed:.3f} '
                f'slope={slope_ms_per_hour(estimate["slope"]):.3f} ms/h '
                f'level_error={error_ms:.3f} ms{reset}'
            )
        return True

    def _update_server_clock_start(
        self,
        server_start_ntp: float,
        received_local: float,
        source: str,
    ) -> None:
        with self._clock_lock:
            self._apply_server_clock_start(
                server_start_ntp, received_local, source
            )

    def _apply_server_clock_start(
        self,
        server_start_ntp: float,
        received_local: float,
        source: str,
    ) -> None:
        previous_start = self._server_clock_start_ntp
        difference = None
        if previous_start is not None:
            difference = server_start_ntp - previous_start
        local_ntp = system_to_ntp_time(received_local)
        client_server_start_difference = None
        if self._client_clock_start_ntp is not None:
            client_server_start_difference = (
                server_start_ntp - self._client_clock_start_ntp
            )
        self._server_clock_start_ntp = server_start_ntp
        self._clock_start_history.append({
            'source': source,
            'local_time': received_local,
            'local_ntp': local_ntp,
            'client_clock_start_ntp': self._client_clock_start_ntp,
            'previous_server_clock_start_ntp': previous_start,
            'server_clock_start_ntp': server_start_ntp,
            'client_server_start_difference': client_server_start_difference,
            'difference': difference,
            'offset': difference,
        })
        if self._debug:
            diff_text = 'None' if difference is None else f'{difference:.9f}'
            print(
                f'{cyan}ECI server clock start update source={source} '
                f'server_start={server_start_ntp:.9f} '
                f'previous_start={previous_start!r} '
                f'start_delta={diff_text}{reset}'
            )

    @check_connected
    def send_command(
        self,
        cmd: str,
        data=None,
        strict: bool = False,
    ) -> object:
        """Send one ECI command and return the parsed server response.

        This is intended for diagnostics and manual testing. Most
        experiment code should use the higher-level methods instead.
        When strict is False, unexpected ECI responses are returned as a
        diagnostic dictionary instead of being raised.
        """
        with self._io_lock:
            return self._command(cmd, data, strict=strict)

    def _format_bytes(self, bytearr: bytes) -> str:
        """Return compact hex/ascii debug text for a byte string."""
        if not isinstance(bytearr, bytes):
            return repr(bytearr)
        hexed = binascii.hexlify(bytearr, sep=' ').decode('ascii')
        ascii_text = ''.join(
            chr(b) if 32 <= b <= 126 else '.'
            for b in bytearr
        )
        return f'len={len(bytearr)} hex=[{hexed}] ascii={ascii_text!r}'

    def _debug_tx(self, cmd: str, data: object, bytearr: bytes) -> None:
        if self._debug:
            print(
                f'{cyan}ECI TX {cmd} data={data!r}: '
                f'{self._format_bytes(bytearr)}{reset}'
            )

    def _debug_rx(self, bytearr: bytes, parsed: object = None) -> None:
        if self._debug:
            print(
                f'{cyan}ECI RX raw: {self._format_bytes(bytearr)} '
                f'parsed={parsed!r}{reset}'
            )

    def _debug_rx_read(self, bytearr: bytes, tokens: list) -> None:
        if self._debug and len(tokens) > 1:
            print(
                f'{cyan}ECI RX stream: {self._format_bytes(bytearr)} '
                f'tokens={len(tokens)}{reset}'
            )

    def _debug_rx_error(self, bytearr: bytes, err: Exception) -> None:
        if self._debug:
            message = getattr(err, 'message', str(err))
            print(
                f'{cyan}ECI RX raw: {self._format_bytes(bytearr)} '
                f'error={type(err).__name__}: {message}{reset}'
            )

    def _remember_eci_error(
        self, cmd: str, record: dict, context: dict = None,
    ) -> None:
        """Keep a failed ECI response where the experiment can find it.

        Non-raising is the default, so without this the only trace of a
        garbled reply would be the error-log file. Recording it here means
        a script can check ``eci_errors()`` (or ``session_summary()``)
        after a run without parsing anything.
        """
        entry = {
            'time': time.time(),
            'cmd': cmd,
            'error': record.get('error'),
            'message': record.get('message'),
            'unexpected': record.get('unexpected'),
            'raw_display': record.get('raw_display'),
        }
        if context:
            entry.update(context)
        # _io_lock is held here; taking _clock_lock inside it is the
        # permitted nesting order.
        with self._clock_lock:
            self._eci_error_count += 1
            self._eci_errors.append(entry)
            if len(self._eci_errors) > self._eci_errors_kept:
                del self._eci_errors[0]

    def eci_errors(self) -> list:
        """Return ECI responses that failed, most recent last.

        These are failures that were recorded rather than raised, which is
        the default behaviour so that one bad reply cannot end a
        recording. Only the most recent ``100`` are kept in memory; the
        error log, if configured, holds every one.
        """
        with self._clock_lock:
            return list(self._eci_errors)

    @check_connected
    def set_strict_eci(self, enabled: bool = True) -> bool:
        """Choose whether failed ECI responses raise or are recorded.

        False (the default) records them -- see :meth:`eci_errors` -- so a
        garbled or rejected reply never interrupts a running experiment.
        True raises instead, which is useful in a diagnostic session where
        you would rather stop at the first sign of trouble.
        """
        with self._clock_lock:
            self._strict_eci = enabled
            return self._strict_eci

    def _response_record(self, bytearr: bytes, err: Exception) -> dict:
        return {
            'ok': False,
            'unexpected': isinstance(err, InvalidECIResponse),
            'error': type(err).__name__,
            'message': getattr(err, 'message', str(err)),
            'raw': bytearr,
            'raw_display': self._format_bytes(bytearr),
        }

    def _clock_snapshot(self) -> dict:
        """Best-effort clock/drift state for attaching to an error record.

        Errors are rare, so paying for a full state snapshot is worth it:
        knowing the drift model was stale, or that fits had been rejected
        for ten minutes, is usually what explains the failure. Never raises
        -- a diagnostic must not become a second failure.
        """
        try:
            return self.clock_state()
        except Exception as err:
            return {'clock_state_error': f'{type(err).__name__}: {err}'}

    def _queue_log_record(self, record: dict) -> None:
        """Hold a log record for writing once the clock lock is released.

        Drift-model transitions are noticed deep inside the regression,
        which runs under ``_clock_lock``. Writing the record there would
        put a filesystem write inside the lock that ``getTime()`` needs,
        so records are parked here and flushed by
        ``_flush_pending_log_records()`` outside the lock instead.
        """
        if not self._error_log:
            return
        record.setdefault('time', time.time())
        self._pending_log_records.append(record)

    def _flush_pending_log_records(self) -> None:
        """Write any parked log records. Must NOT hold ``_clock_lock``."""
        if not self._pending_log_records:
            return
        with self._clock_lock:
            pending = self._pending_log_records
            self._pending_log_records = []
        for record in pending:
            self._append_error_log(record)

    def _append_error_log(self, record: dict) -> None:
        """Append one JSON-lines record. Never raises into the caller."""
        if not self._error_log:
            return
        record.setdefault('time', time.time())
        # Not setdefault(): its argument is evaluated before the key is
        # checked, so callers that opt out with clock=None would still pay
        # for a full clock_state() snapshot that is then discarded.
        if 'clock' not in record:
            record['clock'] = self._clock_snapshot()
        # Serialise the file write, and only the file write. The snapshot
        # above takes _clock_lock, so it is deliberately done first --
        # that keeps _log_lock a leaf that never nests another lock.
        line = json.dumps(record, sort_keys=True, default=str) + '\n'
        try:
            with self._log_lock:
                Path(self._error_log).parent.mkdir(
                    parents=True, exist_ok=True,
                )
                with open(self._error_log, 'a', encoding='utf-8') as logfile:
                    logfile.write(line)
        except Exception as err:
            # Losing the log is bad; losing the recording is worse.
            logger.error(
                'Could not write to error log %s: %s: %s',
                self._error_log, type(err).__name__, err,
            )

    def _write_error_log(
        self,
        cmd: str,
        bytearr: bytes,
        err: Exception,
        context: dict = None,
    ) -> None:
        record = {
            'record': 'eci_response_error',
            'cmd': cmd,
            'error': type(err).__name__,
            'message': getattr(err, 'message', str(err)),
            'unexpected': isinstance(err, InvalidECIResponse),
            'raw_hex': binascii.hexlify(bytearr).decode('ascii'),
            'raw_display': self._format_bytes(bytearr),
        }
        if context:
            record.update(context)
        self._append_error_log(record)

    def _log_unexpected_response(
        self,
        cmd: str,
        bytearr: bytes,
        err: Exception,
        context: dict = None,
    ) -> None:
        logger.warning(
            'Unexpected ECI response for %s: %s; raw=%s',
            cmd,
            getattr(err, 'message', str(err)),
            self._format_bytes(bytearr),
        )
        self._write_error_log(cmd, bytearr, err, context)

    def _log_response_failure(
        self,
        cmd: str,
        bytearr: bytes,
        err: Exception,
        context: dict = None,
    ) -> None:
        logger.error(
            'ECI response failure for %s: %s; raw=%s',
            cmd,
            getattr(err, 'message', str(err)),
            self._format_bytes(bytearr),
        )
        self._write_error_log(cmd, bytearr, err, context)

    def _read_response_token(self, expect: str = 'status') -> bytes:
        """Return the bytes of exactly one complete response.

        Reads until the expected response is whole, consumes precisely
        its bytes, and leaves anything extra buffered for the next
        command. Previously a single recv() was split heuristically, so a
        fragmented response was reported as invalid and the leftovers of
        a multi-byte one were misread as the following command's reply.
        """
        while True:
            token, consumed = frame_response(self._rx_buffer, expect)
            if token is not None:
                self._debug_rx_read(self._rx_buffer, [token])
                self._rx_buffer = self._rx_buffer[consumed:]
                return token
            try:
                chunk = self._socket.read()
            except (socket.timeout, TimeoutError):
                # Nothing more is coming. Two forms are ambiguous until
                # this point -- bare F, and the 8-byte timestamp -- so
                # resolve them now rather than waiting forever.
                token, consumed = frame_response(
                    self._rx_buffer, expect, final=True,
                )
                if token is None:
                    raise
                self._debug_rx_read(self._rx_buffer, [token])
                self._rx_buffer = self._rx_buffer[consumed:]
                return token
            if not chunk:
                # An orderly close: recv() returning b'' means the peer
                # is gone, and no amount of further reading will help.
                self._rx_buffer = b''
                raise SocketException(
                    'Net Station closed the ECI connection'
                )
            self._rx_buffer += chunk

    def _command(
        self,
        cmd: str,
        data=None,
        strict: bool = None,
        context: dict = None,
    ) -> Union[bool, float, int, dict]:
        """Send a command to the amplifier; please do not use as this is
        internal.

        Parameters
        ----------
        cmd: the command to send
        data: the data to send with it
        strict: raise ECI response failures instead of returning a
            diagnostic record. None (the default) follows the connection's
            own setting, which is non-raising unless ``strict_eci=True``
            was given. Applies to unparseable responses and to failures
            the amplifier reports, uniformly
        context: optional fields to attach to any error-log record, so a
            failure can be traced back to the event that caused it

        Returns
        -------
        bool or float or int or dict
            The parsed server response.

        Raises
        ------
        SocketIncompleteTransmission if transmission cannot complete
        InvalidECICommand if the command is invalid

        See Also
        --------
        eci.eci: module for building commands and parsing responses
        """
        if strict is None:
            strict = self._strict_eci
        with self._io_lock:
            if not self._connected:
                raise NetStationUnconnected()
            eci_cmd = build_command(cmd, data)
            self._debug_tx(cmd, data, eci_cmd)
            self._socket.write(eci_cmd)
            response = self._read_response_token(response_shape(cmd))
            try:
                parsed = parse_response(response)
            except InvalidECIResponse as err:
                self._debug_rx_error(response, err)
                self._log_unexpected_response(cmd, response, err, context)
                record = self._response_record(response, err)
                # Honour strict here too. This branch used to return
                # unconditionally, so the one exception the strict flag is
                # documented to control was the one it never reached.
                self._remember_eci_error(cmd, record, context)
                if strict:
                    raise
                return record
            except ECIResponseFailure as err:
                self._debug_rx_error(response, err)
                self._log_response_failure(cmd, response, err, context)
                record = self._response_record(response, err)
                self._remember_eci_error(cmd, record, context)
                if strict:
                    raise
                return record
            self._debug_rx(response, parsed)
            return parsed
