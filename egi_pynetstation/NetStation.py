#!/us/bin/env python
# -*- coding: utf-8 -*-

"""Abstraction of the NetStation SDK as an object"""

import binascii
import json
import logging
import time
from math import floor
from pathlib import Path
from typing import Union

from ntplib import system_to_ntp_time, NTPClient

from .eci import (
    build_command, parse_response, split_response_tokens, allowed_endians,
    package_event,
)
from .socket_wrapper import Socket
from .util import format_time
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
        self._connected = False
        if not (endian in allowed_endians):
            raise NetStationIllegalArgument(endian)
        self._endian = endian
        self._debug = debug
        self._error_log = error_log
        self._mstime = None
        self._syncepoch = None
        self._sync_monotonic = None
        self._sync_system_time = None
        self._client_clock_start_ntp = None
        self._server_clock_start_ntp = None
        self._clock_start_history = []
        self._drift_history = []
        self._drift_correction = False
        self._drift_min_samples = 3
        self._drift_min_span = 30.0
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
    ) -> None:
        """Connect to the Netstation machine via TCP/IP

        Parameters
        ----------
        clock: either 'ntp' or 'simple', indicating clock sync method
        ntp_ip: the IP address of the NTP server on the amplifier
        handshake: send Query and Attention immediately after connecting

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
        if handshake:
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
        monotonic_t = time.monotonic()
        ntp_t = system_to_ntp_time(t + response.offset)
        cresponse = self._command('NTPClockSync', ntp_t)
        self._offset = response.offset
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
        # TODO: Turn into a debug option
        # print('Sent local time: ' + format_time(t))
        # print(f'NTP offset is approx {self._offset}')
        # print(f'Syncepoch is approx {self._syncepoch}')
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
        if not self._ntp_ip:
            raise NetStationNoNTPIP()
        if self._client_clock_start_ntp is None:
            raise RuntimeError('sync_return_clock is unavailable before NTP sync')
        if attention:
            self._command('Attention')
        response = self._command('NTPReturnClock', self._client_clock_start_ntp)
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
        if self._syncepoch is None:
            raise RuntimeError('getTime is unavailable before NTP sync')

        if self._sync_monotonic is None:
            return time.time() - self._syncepoch

        elapsed = time.monotonic() - self._sync_monotonic
        if not self._drift_correction:
            return elapsed

        initial_offset = getattr(self, '_offset', None)
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
        return list(self._clock_start_history)

    def drift_history(self) -> list:
        """Return NTP offset observations used for drift correction."""
        return list(self._drift_history)

    def drift_estimate(self) -> dict:
        """Return the current linear NTP drift estimate."""
        estimate = self._ntp_drift_regression()
        if estimate is None:
            return {
                'enabled': self._drift_correction,
                'samples': len(self._drift_history),
                'min_samples': self._drift_min_samples,
                'min_span': self._drift_min_span,
                'slope': None,
                'intercept': None,
                'predicted_offset': getattr(self, '_offset', None),
                'initial_offset': getattr(self, '_offset', None),
            }
        elapsed = None
        if self._sync_monotonic is not None:
            elapsed = time.monotonic() - self._sync_monotonic
        predicted = self._predict_ntp_offset(elapsed)
        return {
            'enabled': self._drift_correction,
            'samples': len(self._drift_history),
            'min_samples': self._drift_min_samples,
            'min_span': self._drift_min_span,
            'slope': estimate['slope'],
            'intercept': estimate['intercept'],
            'predicted_offset': predicted,
            'initial_offset': getattr(self, '_offset', None),
            'elapsed': elapsed,
        }

    @check_connected
    def set_drift_correction(self, enabled: bool = True) -> bool:
        """Enable or disable drift-corrected getTime()."""
        self._drift_correction = enabled
        return self._drift_correction

    @check_connected
    def set_drift_requirements(
        self,
        min_samples: int = 3,
        min_span: float = 30.0,
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
        self._drift_min_samples = min_samples
        self._drift_min_span = min_span
        return {
            'min_samples': self._drift_min_samples,
            'min_span': self._drift_min_span,
        }

    @check_connected
    def sample_drift(self) -> dict:
        """Query the NTP server and record an offset sample.

        This does not send any ECI clock-sync command. It only asks the
        amplifier/Net Station NTP server for the current NTP offset so that
        client-side timestamps can be drift-corrected.
        """
        if not self._ntp_ip:
            raise NetStationNoNTPIP()
        if self._syncepoch is None:
            raise RuntimeError('sample_drift is unavailable before NTP sync')
        c = NTPClient()
        response = c.request(self._ntp_ip, version=3)
        return self._record_ntp_drift_sample(response, source='drift_sample')

    def clock_state(self) -> dict:
        """Return current client/server clock synchronization state."""
        drift = self.drift_estimate()
        return {
            'client_clock_start_ntp': self._client_clock_start_ntp,
            'server_clock_start_ntp': self._server_clock_start_ntp,
            'syncepoch': self._syncepoch,
            'sync_monotonic': self._sync_monotonic,
            'ntp_offset': getattr(self, '_offset', None),
            'drift_correction': self._drift_correction,
            'drift_samples': len(self._drift_history),
            'drift_min_samples': self._drift_min_samples,
            'drift_min_span': self._drift_min_span,
            'drift_slope': drift.get('slope'),
            'predicted_ntp_offset': drift.get('predicted_offset'),
        }

    def _record_ntp_drift_sample(
        self,
        response,
        source: str,
        local_time: float = None,
        monotonic_time: float = None,
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
        self._drift_history.append(sample)
        if self._debug:
            elapsed_text = 'None' if elapsed is None else f'{elapsed:.6f}'
            print(
                f'{cyan}NTP drift sample source={source} '
                f'elapsed={elapsed_text} offset={response.offset:.9f} '
                f'delay={response.delay:.9f}{reset}'
            )
        return dict(sample)

    def _ntp_drift_regression(self):
        samples = [
            sample for sample in self._drift_history
            if sample.get('elapsed') is not None
        ]
        if len(samples) < self._drift_min_samples:
            return None

        span = samples[-1]['elapsed'] - samples[0]['elapsed']
        if span < self._drift_min_span:
            return None

        xs = [sample['elapsed'] for sample in samples]
        ys = [sample['offset'] for sample in samples]
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        denom = sum((x - mean_x) ** 2 for x in xs)
        if denom == 0:
            return None
        slope = sum((x - mean_x) * (y - mean_y)
                    for x, y in zip(xs, ys)) / denom
        intercept = mean_y - slope * mean_x
        return {
            'slope': slope,
            'intercept': intercept,
        }

    def _predict_ntp_offset(self, elapsed: float = None):
        if elapsed is None:
            if self._sync_monotonic is None:
                return getattr(self, '_offset', None)
            elapsed = time.monotonic() - self._sync_monotonic

        estimate = self._ntp_drift_regression()
        if estimate is None:
            if self._drift_history:
                return self._drift_history[-1]['offset']
            return getattr(self, '_offset', None)
        return estimate['intercept'] + estimate['slope'] * elapsed

    def _update_server_clock_start(
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
