#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Abstraction of the NetStation SDK as an object"""

import binascii
import json
import logging
import threading
import time
from pathlib import Path
from typing import Union

from ntplib import system_to_ntp_time, NTPClient

from .eci import (
    build_command, parse_response, split_response_tokens, allowed_endians,
    package_event,
)
from .socket_wrapper import Socket
from .exceptions import *

cyan = '\u001b[36;1m'
reset = '\u001b[0m'
logger = logging.getLogger(__name__)


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
        ipv4: the ipv4 address to use for the amplifier
        port: the port number to use for the amplifier
        endian: the endianness of the machine; see eci.allowed_endians
        debug: print ECI command and response bytes when True
        error_log: optional path for JSON-lines ECI error records


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
        self._mstime = None
        self._syncepoch = None
        self._offset_mono = None
        self._sync_monotonic = None
        self._sync_system_time = None
        self._client_clock_start_ntp = None
        self._server_clock_start_ntp = None
        self._clock_start_history = []
        self._drift_history = []
        self._drift_correction = True
        self._drift_min_samples = 13
        self._drift_min_span = 180.0
        self._drift_max_delay = 0.010
        self._drift_max_residual = 0.003
        self._drift_window = 15 * 60.0
        self._drift_max_model_age = 600.0
        self._drift_slew = 0.0002
        self._drift_samples_per_call = 4
        self._drift_sample_spacing = 0.05
        self._drift_model = None
        self._drift_model_dirty = True
        self._drift_active_model = None
        self._drift_last_reject_reason = None
        self._drift_rejected_fits = 0
        self._drift_accepted_fits = 0
        self._auto_drift_enabled = False
        self._auto_drift_interval = 60.0
        self._auto_drift_min_pause = 0.5
        self._auto_drift_last_monotonic = None
        self._response_tokens = []
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
    ) -> None:
        """Connect to the Netstation machine via TCP/IP

        Parameters
        ----------
        clock: either 'ntp' or 'simple', indicating clock sync method
        ntp_ip: the IP address of the NTP server on the amplifier
        handshake: send Query and Attention immediately after connecting
        drift_correction: enable client-side drift correction for getTime()
        drift_min_samples: optional minimum number of NTP samples needed before
            applying drift correction
        drift_min_span: optional minimum sample window, in seconds, needed
            before applying drift correction
        drift_max_delay: optional maximum NTP round-trip delay, in seconds,
            for samples used by the drift model
        drift_max_residual: optional maximum absolute model residual, in
            seconds, before a fitted drift line is rejected
        drift_window_minutes: optional rolling window, in minutes, used by the
            drift model. Use 0 to fit all valid drift samples.
        drift_samples: optional number of NTP queries per drift sample; the
            lowest-delay reply in the burst is kept
        drift_sample_spacing: optional seconds between queries within a burst
        drift_slew: optional maximum rate, in seconds of correction per second
            of elapsed time, at which level errors are retired
        drift_max_model_age: optional maximum seconds a fitted slope may be
            extrapolated past its anchor; 0 for unbounded

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
        self._drift_correction = drift_correction
        if drift_min_samples is not None or drift_min_span is not None:
            self.set_drift_requirements(
                min_samples=(
                    self._drift_min_samples
                    if drift_min_samples is None
                    else drift_min_samples
                ),
                min_span=(
                    self._drift_min_span
                    if drift_min_span is None
                    else drift_min_span
                ),
            )
        if drift_max_delay is not None or drift_window_minutes is not None:
            self.set_drift_model_options(
                max_delay=drift_max_delay,
                max_residual=drift_max_residual,
                window_minutes=drift_window_minutes,
            )
        if drift_samples is not None or drift_sample_spacing is not None:
            self.set_drift_sampling(
                samples=drift_samples,
                spacing=drift_sample_spacing,
            )
        if drift_slew is not None or drift_max_model_age is not None:
            self.set_drift_stability(
                slew=drift_slew,
                max_model_age=drift_max_model_age,
            )
        if handshake:
            self._command('Query', self._endian)
            self._command('Attention')

    @check_connected
    def ntpsync(self):
        """Perform the ECI NTP synchronization.

        This sends one ECI ``NTPClockSync`` command using the current offset
        reported by the amplifier/Net Station NTP server. It also stores the
        first NTP offset sample used by the client-side drift corrector.
        Repeated ECI clock syncs during a recording can reset the local event
        timestamp epoch and should be avoided for normal experiments.
        """
        if not self._ntp_ip:
            raise NetStationNoNTPIP()
        c = NTPClient()
        response = c.request(self._ntp_ip, version=3)
        t = time.time()
        monotonic_t = time.monotonic()
        ntp_t = system_to_ntp_time(t + response.offset)
        with self._io_lock:
            self._ntpsynced = True
            self._command('Attention')
            cresponse = self._command('NTPClockSync', ntp_t)
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
            self._client_clock_start_ntp = ntp_t
            self._record_ntp_drift_sample(
                response,
                source='ntpsync',
                local_time=t,
                monotonic_time=monotonic_t,
            )
            return cresponse

    @check_connected
    def resync(self, attention: bool = False):
        """Backward-compatible alias for sync_return_clock()."""
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

        Parameters
        ----------
        attention: send Attention immediately before NTPReturnClock. This
        defaults to False because some Net Station configurations appear to
        suppress the delayed timestamp response after Attention.
        max_followups: number of resy events to send while waiting for the
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
                event_response = self.send_event(event_type="resy", label='resy')
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
        time_at_monotonic: convert a previously captured ``time.monotonic()``
        reading into an event timestamp. Prefer that in a screen-flip
        callback: capture the raw monotonic value on the critical path and
        convert it afterwards, so no lock or model work happens near the
        flip.
        """
        return self.time_at_monotonic(time.monotonic())

    @check_connected
    def time_at_monotonic(self, monotonic_time: float):
        """Return the event timestamp for a captured monotonic reading.

        Parameters
        ----------
        monotonic_time:
            A value previously returned by ``time.monotonic()``.

        Notes
        -----
        This lets latency-critical code record ``time.monotonic()`` with no
        locking and no drift-model work, then convert to a package
        timestamp later, off the critical path. The resulting timestamp
        describes the instant of capture, not the instant of conversion.
        """
        with self._clock_lock:
            if self._syncepoch is None:
                raise RuntimeError('getTime is unavailable before NTP sync')

            if self._sync_monotonic is None:
                return time.time() - self._syncepoch

            # Use monotonic time for elapsed event timestamps so wall-clock
            # adjustments on the stimulus computer do not create timestamp jumps.
            elapsed = monotonic_time - self._sync_monotonic
            if not self._drift_correction:
                return elapsed

            initial_offset = getattr(self, '_offset_mono', None)
            predicted_offset = self._predict_ntp_offset(elapsed)
            if initial_offset is None or predicted_offset is None:
                return elapsed
            return elapsed + (predicted_offset - initial_offset)
    
    
    @check_connected
    def resync_do_not_use_not_recommended(self):
        """Backward-compatible alias for resync()."""
        return self.resync()

    @check_connected
    def disconnect(self) -> None:
        """Close the TCP/IP connection."""
        with self._io_lock:
            self._command('Exit')
            self._socket.disconnect()
            self._connected = False

    @check_connected
    def begin_rec(self) -> None:
        """Begin recording and perform the initial ECI NTP sync."""
        with self._io_lock:
            if self._ntp_ip:
                self._command('BeginRecording')
                self._recording_start = time.time()
                self.ntpsync()
                return

            raise NetStationNoNTPIP()

    @check_connected
    def end_rec(self) -> None:
        """End Recording"""
        with self._io_lock:
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
        # The timestamp is resolved before the socket lock is taken, so a
        # send in progress on another thread can never delay it.
        if start == 'now':
            start = self.getTime()
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
        with self._io_lock:
            return self._command('EventData', data)

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

    def clock_offsets(self) -> list:
        """Return server clock-start observations collected by resync()."""
        with self._clock_lock:
            return list(self._clock_start_history)

    def drift_history(self) -> list:
        """Return NTP offset observations used for drift correction."""
        with self._clock_lock:
            return list(self._drift_history)

    def drift_estimate(self) -> dict:
        """Return the current linear NTP drift estimate.

        The slope is expressed as seconds of NTP offset change per second of
        local elapsed time. Multiply by ``1000 * 3600`` to express it as
        milliseconds per hour.
        """
        with self._clock_lock:
            estimate = self._ntp_drift_regression()
            valid_samples = self._valid_drift_samples(windowed=False)
            rejected_samples = len(self._drift_history) - len(valid_samples)
            if estimate is None:
                elapsed = None
                if self._sync_monotonic is not None:
                    elapsed = time.monotonic() - self._sync_monotonic
                predicted = self._predict_active_ntp_offset(elapsed)
                if predicted is None:
                    predicted = getattr(self, '_offset_mono', None)
                return {
                    'enabled': self._drift_correction,
                    'samples': len(self._drift_history),
                    'valid_samples': len(valid_samples),
                    'rejected_samples': rejected_samples,
                    'model_samples': 0,
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
                    'slope': None,
                    'intercept': None,
                    'predicted_offset': predicted,
                    'initial_offset': getattr(self, '_offset_mono', None),
                    'elapsed': elapsed,
                    'active_slope': (
                        None if self._drift_active_model is None
                        else self._drift_active_model.get('slope')
                    ),
                    'active_anchor_elapsed': (
                        None if self._drift_active_model is None
                        else self._drift_active_model.get('anchor_elapsed')
                    ),
                    'active_anchor_offset': (
                        None if self._drift_active_model is None
                        else self._drift_active_model.get('anchor_offset')
                    ),
                }
            elapsed = None
            if self._sync_monotonic is not None:
                elapsed = time.monotonic() - self._sync_monotonic
            predicted = self._predict_ntp_offset(elapsed)
            return {
                'enabled': self._drift_correction,
                'samples': len(self._drift_history),
                'valid_samples': len(valid_samples),
                'rejected_samples': rejected_samples,
                'model_samples': estimate['sample_count'],
                'min_samples': self._drift_min_samples,
                'min_span': self._drift_min_span,
                'max_delay': self._drift_max_delay,
                'max_residual': self._drift_max_residual,
                'window': self._drift_window,
                'window_minutes': (
                    None if self._drift_window is None
                    else self._drift_window / 60.0
                ),
                'model_span': estimate['span'],
                'model_cached': not self._drift_model_dirty,
                'model_updated_local_time': estimate['updated_local_time'],
                'model_max_residual': estimate['max_residual'],
                'model_rms_residual': estimate['rms_residual'],
                'slope': estimate['slope'],
                'intercept': estimate['intercept'],
                'predicted_offset': predicted,
                'initial_offset': getattr(self, '_offset_mono', None),
                'elapsed': elapsed,
                'active_slope': (
                    None if self._drift_active_model is None
                    else self._drift_active_model.get('slope')
                ),
                'active_anchor_elapsed': (
                    None if self._drift_active_model is None
                    else self._drift_active_model.get('anchor_elapsed')
                ),
                'active_anchor_offset': (
                    None if self._drift_active_model is None
                    else self._drift_active_model.get('anchor_offset')
                ),
            }

    @check_connected
    def set_drift_correction(self, enabled: bool = True) -> bool:
        """Enable or disable drift-corrected getTime()."""
        with self._clock_lock:
            self._drift_correction = enabled
            return self._drift_correction

    @check_connected
    def set_drift_requirements(
        self,
        min_samples: int = 4,
        min_span: float = 90.0,
    ) -> dict:
        """Set minimum evidence needed before applying drift correction.

        Parameters
        ----------
        min_samples:
            Minimum number of NTP offset observations needed before fitting a
            drift line.
        min_span:
            Minimum time window, in seconds, between the first and most recent
            drift samples before the fitted correction is applied.
        """
        if min_samples < 2:
            raise ValueError('min_samples must be at least 2')
        if min_span < 0:
            raise ValueError('min_span must be non-negative')
        with self._clock_lock:
            self._drift_min_samples = min_samples
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
        enabled: bool = True,
        interval: float = None,
        min_pause: float = None,
    ) -> dict:
        """Configure the schedule used by ``sample_drift_if_due()``.

        Parameters
        ----------
        enabled:
            Whether ``sample_drift_if_due()`` may take samples at all.
        interval:
            Target seconds between drift samples.
        min_pause:
            Minimum idle time, in seconds, an experiment must be able to
            offer before a sample is taken.
        """
        with self._clock_lock:
            self._auto_drift_enabled = enabled
            if interval is not None:
                if interval <= 0:
                    raise ValueError('interval must be positive')
                self._auto_drift_interval = interval
            if min_pause is not None:
                if min_pause < 0:
                    raise ValueError('min_pause must be non-negative')
                self._auto_drift_min_pause = min_pause
            return {
                'enabled': self._auto_drift_enabled,
                'interval': self._auto_drift_interval,
                'min_pause': self._auto_drift_min_pause,
            }

    @check_connected
    def set_drift_stability(
        self,
        slew: float = None,
        max_model_age: float = None,
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
            return {
                'slew': self._drift_slew,
                'max_model_age': self._drift_max_model_age,
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
                elapsed = time.monotonic() - self._sync_monotonic
            estimate = self._ntp_drift_regression()
            if estimate is not None:
                self._activate_drift_model(estimate, elapsed)
            return self.drift_estimate()

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
                response = client.request(self._ntp_ip, version=3)
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
        response, burst = self._query_ntp_best_of(samples, spacing)
        with self._clock_lock:
            self._auto_drift_last_monotonic = time.monotonic()
            return self._record_ntp_drift_sample(
                response,
                source='drift_sample',
                burst=burst,
            )

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

    def clock_state(self) -> dict:
        """Return current client/server clock synchronization state."""
        with self._clock_lock:
            drift = self.drift_estimate()
            return {
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
                        time.monotonic() - self._sync_monotonic
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
        if local_time is None:
            local_time = time.time()
        if monotonic_time is None:
            monotonic_time = time.monotonic()
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
        # ntplib reports the offset against the local *system* clock, but
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
        return dict(sample)

    def _ntp_drift_regression(self):
        if not self._drift_model_dirty:
            return self._drift_model

        self._drift_model = self._fit_ntp_drift_regression()
        self._drift_model_dirty = False
        return self._drift_model

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

    def _valid_drift_samples(self, windowed: bool = True) -> list:
        samples = [
            sample for sample in self._drift_history
            if (
                sample.get('elapsed') is not None and
                sample.get('valid', True)
            )
        ]
        if not windowed or not samples or self._drift_window is None:
            return samples
        latest_elapsed = samples[-1]['elapsed']
        window_start = latest_elapsed - self._drift_window
        return [
            sample for sample in samples
            if sample['elapsed'] >= window_start
        ]

    def _predict_ntp_offset(self, elapsed: float = None):
        if elapsed is None:
            if self._sync_monotonic is None:
                return getattr(self, '_offset_mono', None)
            elapsed = time.monotonic() - self._sync_monotonic

        estimate = self._ntp_drift_regression()
        if estimate is None:
            active_offset = self._predict_active_ntp_offset(elapsed)
            if active_offset is not None:
                return active_offset
            return getattr(self, '_offset_mono', None)
        self._activate_drift_model(estimate, elapsed)
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

    def _activate_drift_model(self, estimate: dict, elapsed: float) -> None:
        model_id = self._drift_model_id(estimate)
        active = self._drift_active_model
        if active is not None and active.get('model_id') == model_id:
            return
        if elapsed is None:
            return

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
                f'slope={estimate["slope"] * 1000.0 * 3600.0:.3f} ms/h '
                f'level_error={error_ms:.3f} ms{reset}'
            )

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

    def _local_ntp_now(self) -> float:
        offset = getattr(self, '_offset', 0)
        return system_to_ntp_time(time.time() + offset)

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

    def _response_record(self, bytearr: bytes, err: Exception) -> dict:
        return {
            'ok': False,
            'unexpected': isinstance(err, InvalidECIResponse),
            'error': type(err).__name__,
            'message': getattr(err, 'message', str(err)),
            'raw': bytearr,
            'raw_display': self._format_bytes(bytearr),
        }

    def _write_error_log(
        self,
        cmd: str,
        bytearr: bytes,
        err: Exception,
    ) -> None:
        if not self._error_log:
            return
        record = {
            'time': time.time(),
            'cmd': cmd,
            'error': type(err).__name__,
            'message': getattr(err, 'message', str(err)),
            'unexpected': isinstance(err, InvalidECIResponse),
            'raw_hex': binascii.hexlify(bytearr).decode('ascii'),
            'raw_display': self._format_bytes(bytearr),
        }
        Path(self._error_log).parent.mkdir(parents=True, exist_ok=True)
        with open(self._error_log, 'a', encoding='utf-8') as logfile:
            logfile.write(json.dumps(record, sort_keys=True) + '\n')

    def _log_unexpected_response(
        self,
        cmd: str,
        bytearr: bytes,
        err: Exception,
    ) -> None:
        logger.warning(
            'Unexpected ECI response for %s: %s; raw=%s',
            cmd,
            getattr(err, 'message', str(err)),
            self._format_bytes(bytearr),
        )
        self._write_error_log(cmd, bytearr, err)

    def _log_response_failure(
        self,
        cmd: str,
        bytearr: bytes,
        err: Exception,
    ) -> None:
        logger.error(
            'ECI response failure for %s: %s; raw=%s',
            cmd,
            getattr(err, 'message', str(err)),
            self._format_bytes(bytearr),
        )
        self._write_error_log(cmd, bytearr, err)

    def _read_response_token(self) -> bytes:
        if not self._response_tokens:
            response = self._socket.read()
            tokens = split_response_tokens(response)
            self._debug_rx_read(response, tokens)
            self._response_tokens.extend(tokens)
        if self._response_tokens:
            return self._response_tokens.pop(0)
        return b''

    def _command(
        self,
        cmd: str,
        data=None,
        strict: bool = True,
    ) -> Union[bool, float, int, dict]:
        """Send a command to the amplifier; please do not use as this is
        internal.

        Parameters
        ----------
        cmd: the command to send
        data: the data to send with it
        strict: raise ECI response parsing exceptions when True

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
        with self._io_lock:
            if not self._connected:
                raise NetStationUnconnected()
            eci_cmd = build_command(cmd, data)
            self._debug_tx(cmd, data, eci_cmd)
            self._socket.write(eci_cmd)
            response = self._read_response_token()
            try:
                parsed = parse_response(response)
            except InvalidECIResponse as err:
                self._debug_rx_error(response, err)
                self._log_unexpected_response(cmd, response, err)
                return self._response_record(response, err)
            except ECIResponseFailure as err:
                self._debug_rx_error(response, err)
                self._log_response_failure(cmd, response, err)
                if strict:
                    raise
                return self._response_record(response, err)
            self._debug_rx(response, parsed)
            return parsed
