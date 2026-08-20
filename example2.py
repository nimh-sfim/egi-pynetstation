#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Interactive ECI command sender for NetStation debugging.

Run this from a terminal and press the displayed keys to send individual
messages. Debug output is enabled by default, so each command prints the
bytes sent to Net Station and the raw/parsed response.
"""

import sys
import time
from argparse import ArgumentParser
from contextlib import redirect_stdout
from pathlib import Path
from typing import Callable

from ntplib import system_to_ntp_time

from egi_pynetstation.NetStation import NetStation
from egi_pynetstation.eci import package_event


def _message(err: Exception) -> str:
    return getattr(err, 'message', str(err))


def connect_with_drift_options(
    ns: NetStation,
    ntp_ip: str,
    handshake: bool,
    args,
) -> None:
    """Connect using this repository's drift options.

    This used to fall back to an older connect() API when the imported
    package did not accept these arguments. That was the wrong call for a
    diagnostic tool: the fallback quietly ran a different code path, and
    then the console reported drift state for a configuration nobody had
    asked for. Fail loudly instead, exactly as example5 does -- the usual
    cause is an older copy of the package shadowing this repository.
    """
    try:
        ns.connect(
            ntp_ip=ntp_ip,
            handshake=handshake,
            drift_correction=not args.no_drift_correction,
            drift_min_samples=args.drift_min_samples,
            drift_min_span=args.drift_min_span,
            drift_max_delay=args.drift_max_delay,
            drift_max_residual=args.drift_max_residual,
            drift_window_minutes=args.drift_window_minutes,
        )
    except TypeError as err:
        raise SystemExit(
            'The imported egi_pynetstation does not accept the drift '
            'options this console configures, so it would report drift '
            'state for a session it did not actually set up.\n'
            f'  error: {err}\n'
            f'  loaded from: {NetStation.__module__}\n'
            'Install this repository with `pip install -e .` and remove '
            'any older copy with `pip uninstall egi_pynetstation`.'
        )


def print_result(result: object, ns: NetStation = None) -> None:
    if isinstance(result, dict) and not result.get('ok', True):
        label = 'Unexpected response' if result.get('unexpected') else 'ECI response error'
        print(f'{label}: {result.get("error")}: {result.get("message")}')
        print(f'Raw response: {result.get("raw_display")}')
    else:
        print(f'Parsed result: {result!r}')
        if isinstance(result, float) and ns is not None:
            try:
                package_time = ns.getTime()
            except Exception as err:
                print(f'getTime unavailable: {type(err).__name__}: {_message(err)}')
            else:
                print('Timestamp context:')
                print(f'  parsed response: {result:.9f}')
                print(f'  getTime():       {package_time:.9f}')
                print('  note: NTPReturnClock responses are clock-start times,')
                print('        while getTime() is elapsed time since client sync.')
                state = ns.clock_state()
                client_start = state.get('client_clock_start_ntp')
                server_start = state.get('server_clock_start_ntp')
                if client_start is not None and server_start is not None:
                    print(
                        '  server-client clock-start delta: '
                        f'{server_start - client_start:.9f}'
                    )


def print_offsets(ns: NetStation) -> None:
    history = ns.clock_offsets()
    if not history:
        print('\nNo clock offset observations yet.')
        return

    print('\nServer clock-start observations:')
    print('idx  source    local_elapsed_s  server_start_delta_s  client-server_start_s')
    t0 = history[0]['local_time']
    for idx, item in enumerate(history):
        elapsed = item['local_time'] - t0
        difference = item.get('difference', item.get('offset'))
        diff_text = 'n/a' if difference is None else f'{difference:> .9f}'
        start_difference = item.get('client_server_start_difference')
        start_diff_text = (
            'n/a' if start_difference is None
            else f'{start_difference:> .9f}'
        )
        print(
            f'{idx:>3}  {item["source"]:<8}  '
            f'{elapsed:>15.6f}  {diff_text}  {start_diff_text}'
        )

    regression_history = [
        item for item in history
        if item.get('difference', item.get('offset')) is not None
    ]
    if len(regression_history) < 2:
        print('Need at least two observations for a linear drift estimate.')
        return

    xs = [item['local_time'] - t0 for item in regression_history]
    ys = [
        item.get('difference', item.get('offset'))
        for item in regression_history
    ]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        print('Need separated observations for a linear drift estimate.')
        return
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
    intercept = mean_y - slope * mean_x
    predicted_now = intercept + slope * (time.time() - t0)
    print('Linear server-start stability estimate:')
    print(f'  delta = {intercept:.9f} + {slope:.12f} * elapsed_seconds')
    print(f'  predicted server-start delta now: {predicted_now:.9f} s')


def print_session_summary(ns: NetStation) -> None:
    """One-line health verdict, plus what to look at when it is False."""
    summary = ns.session_summary()
    print('\nSession summary:')
    print(f"  ok: {summary['ok']}")
    for key in (
        'drift_engaged',
        'drift_stalled',
        'drift_accepted_fits',
        'drift_rejected_fits',
        'drift_samples',
        'active_drift_slope_ms_per_hour',
        'event_send_failures',
        'eci_response_failures',
        'ntp_sampling_stale',
        'ntp_sample_failures',
        'ntp_seconds_since_success',
    ):
        print(f'  {key}: {summary[key]}')


def print_clock_state(ns: NetStation) -> None:
    state = ns.clock_state()
    print('\nClock state:')
    for key in (
        'client_clock_start_ntp',
        'server_clock_start_ntp',
        'syncepoch',
        'sync_monotonic',
        'ntp_offset',
        'drift_correction',
        'drift_samples',
        'drift_min_samples',
        'drift_min_span',
        'drift_max_delay',
        'drift_window',
        'drift_valid_samples',
        'drift_rejected_samples',
        'drift_model_samples',
        'drift_model_span',
        'drift_slope',
        'predicted_ntp_offset',
    ):
        value = state.get(key)
        if isinstance(value, float):
            print(f'  {key}: {value:.9f}')
        else:
            print(f'  {key}: {value!r}')


def print_drift(ns: NetStation) -> None:
    history = ns.drift_history()
    estimate = ns.drift_estimate()
    if not history:
        print('\nNo NTP drift samples yet.')
        return

    print('\nNTP drift samples:')
    print(
        'idx  source        elapsed_s       offset_s       delta_s       '
        'delay_s  status'
    )
    first_offset = history[0]['offset']
    for idx, sample in enumerate(history):
        elapsed = sample.get('elapsed')
        elapsed_text = 'n/a' if elapsed is None else f'{elapsed:>12.6f}'
        delta = sample['offset'] - first_offset
        print(
            f'{idx:>3}  {sample["source"]:<12}  {elapsed_text}  '
            f'{sample["offset"]:>13.9f}  {delta:>12.9f}  '
            f'{sample["delay"]:>10.6f}  '
            f'{sample.get("reject_reason") or "ok"}'
        )

    print('NTP drift estimate:')
    print(f'  correction enabled: {estimate.get("enabled")}')
    print(f'  samples: {estimate.get("samples")}')
    print(f'  valid samples: {estimate.get("valid_samples")}')
    print(f'  rejected samples: {estimate.get("rejected_samples")}')
    print(f'  model samples: {estimate.get("model_samples")}')
    print(
        '  correction requirements: '
        f'{estimate.get("min_samples")} samples over '
        f'{estimate.get("min_span")} s'
    )
    window_minutes = estimate.get("window_minutes")
    window_text = (
        'all valid samples' if window_minutes is None
        else f'last {window_minutes} min'
    )
    print(
        '  sample quality/window: '
        f'max delay {estimate.get("max_delay")} s, '
        f'max residual {estimate.get("max_residual")} s, '
        f'{window_text}'
    )
    slope = estimate.get('slope')
    if slope is None:
        print('  need at least two separated samples for a linear estimate')
    else:
        print(f'  offset slope: {slope:.12f} s/s')
        print(f'  offset drift: {slope * 1000 * 3600:.6f} ms/hour')
        active_slope = estimate.get('active_slope')
        if active_slope is not None:
            print(
                '  active applied drift: '
                f'{active_slope * 1000 * 3600:.6f} ms/hour'
            )
        print(f'  model span: {estimate.get("model_span"):.6f} s')
        print(
            '  predicted offset now: '
            f'{estimate.get("predicted_offset"):.9f} s'
        )
    try:
        print(f'  getTime() now: {ns.getTime():.9f} s')
    except Exception as err:
        print(f'  getTime unavailable: {type(err).__name__}: {_message(err)}')


def strip_comment(line: str) -> str:
    return line.split('#', 1)[0].strip()


def load_experiment(path: str) -> list:
    steps = []
    for lineno, line in enumerate(Path(path).read_text().splitlines(), 1):
        command = strip_comment(line)
        if command:
            steps.append((lineno, command))
    return steps


class Tee:
    def __init__(self, *streams):
        self._streams = streams

    def write(self, text: str) -> None:
        for stream in self._streams:
            stream.write(text)

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()


def read_key() -> str:
    """Read a single keypress, falling back to input() when needed."""
    if not sys.stdin.isatty():
        text = input('command> ')
        return text[:1]

    try:
        import termios
        import tty
    except ImportError:
        text = input('command> ')
        return text[:1]

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def print_menu(connected: bool, profile: str, ip_cmd: str, port_cmd: int,
               ip_clock: str, endian: str) -> None:
    status = 'connected' if connected else 'not connected'
    print('\n' + '=' * 72)
    print('EGI PyNetStation manual command sender')
    print(f'Profile: {profile} | ECI: {ip_cmd}:{port_cmd} | '
          f'NTP: {ip_clock} | endian: {endian} | {status}')
    print('-' * 72)
    print('c  connect socket only        h  connect with Query + Attention')
    print('q  Query                      y  NewQuery')
    print('a  Attention                  t  ClockSync')
    print('n  raw NTPClockSync           i  high-level ntpsync')
    print('s  sync return clock          S  sync return clock + Attention')
    print('p  raw NTPReturnClock         b  BeginRecording')
    print('e  EndRecording               v  EventData simple')
    print('d  EventData with data')
    print('r  sample NTP drift           g  show NTP drift')
    print('R  refit drift model          G  configure drift window')
    print('M  configure drift model')
    print('o  show offset history        k  show clock state')
    print('x  Exit + close socket')
    print('?  redraw menu                Ctrl-C or Ctrl-D to quit')
    print('=' * 72)
    print('Press a key: ', end='', flush=True)


def make_simple_event() -> bytes:
    return package_event(
        start=0.0,
        duration=0.001,
        event_type='TEST',
        label='manual test',
        desc='Sent from example2.py',
        data={},
    )


def make_data_event() -> bytes:
    return package_event(
        start=0.0,
        duration=0.001,
        event_type='DATA',
        label='manual data test',
        desc='Sent from example2.py with key-value data',
        data={
            'bool': True,
            'doub': 1.25,
            'long': 42,
            'TEXT': 'hello',
        },
    )


def main() -> None:
    p = ArgumentParser(description='Interactively send NetStation ECI commands')
    p.add_argument(
        'mode',
        choices=['local', 'amp', 'custom'],
        help='local uses AmpServer Testing Applications; amp uses common EGI IPs',
    )
    p.add_argument('--ip-cmd', help='Net Station / command IPv4 address')
    p.add_argument('--ip-clock', help='NTP server / amplifier IPv4 address')
    p.add_argument('--port', type=int, help='ECI TCP port')
    p.add_argument(
        '--endian',
        choices=['NTEL', 'MAC-', 'UNIX'],
        default='NTEL',
        help='Endian string for Query',
    )
    p.add_argument(
        '--quiet',
        action='store_true',
        help='Disable NetStation TX/RX debug printing',
    )
    p.add_argument(
        '--error-log',
        help='Append JSON-lines ECI response errors to this file',
    )
    p.add_argument(
        '--no-drift-correction',
        action='store_true',
        help='Disable client-side NTP drift correction',
    )
    p.add_argument(
        '--drift-min-samples',
        type=int,
        default=13,
        help='Minimum NTP samples before applying drift correction',
    )
    p.add_argument(
        '--drift-min-span',
        type=float,
        default=180.0,
        help='Minimum drift sampling window, in seconds',
    )
    p.add_argument(
        '--drift-max-delay',
        type=float,
        default=0.010,
        help='Reject NTP drift samples above this round-trip delay, in seconds',
    )
    p.add_argument(
        '--drift-max-residual',
        type=float,
        default=0.003,
        help=(
            'Reject NTP drift fits above this maximum absolute residual, '
            'in seconds'
        ),
    )
    p.add_argument(
        '--drift-window-minutes',
        type=float,
        default=15.0,
        help=(
            'Use only the last N minutes of valid drift samples for the model; '
            '0 uses all valid samples'
        ),
    )
    p.add_argument(
        '--experiment',
        help='Run commands from an experiment text file before interactive mode',
    )
    p.add_argument(
        '--no-interactive',
        action='store_true',
        help='Exit after --experiment instead of entering keypress mode',
    )
    p.add_argument(
        '--transcript',
        help='Append console output to this text file',
    )
    args = p.parse_args()
    if args.drift_max_delay <= 0:
        p.error('--drift-max-delay must be positive')
    if args.drift_max_residual <= 0:
        p.error('--drift-max-residual must be positive')
    if args.drift_window_minutes < 0:
        p.error('--drift-window-minutes must be non-negative')

    if args.mode == 'local':
        ip_cmd = args.ip_cmd or '127.0.0.1'
        ip_clock = args.ip_clock or '216.239.35.4'
        port_cmd = args.port or 9885
    elif args.mode == 'amp':
        ip_cmd = args.ip_cmd or '10.10.10.42'
        ip_clock = args.ip_clock or '10.10.10.51'
        port_cmd = args.port or 55513
    else:
        if not (args.ip_cmd and args.ip_clock and args.port):
            p.error('custom mode requires --ip-cmd, --ip-clock, and --port')
        ip_cmd = args.ip_cmd
        ip_clock = args.ip_clock
        port_cmd = args.port

    ns = NetStation(
        ip_cmd,
        port_cmd,
        endian=args.endian,
        debug=not args.quiet,
        error_log=args.error_log,
    )

    def ensure_connected() -> bool:
        if ns._connected:
            return True
        print('\nNot connected. Press c or h first.')
        return False

    def run(label: str, action: Callable[[], object]) -> None:
        print(f'\n--- {label} ---')
        try:
            result = action()
        except Exception as err:
            print(f'{type(err).__name__}: {_message(err)}')
        else:
            print_result(result, ns)

    def connect_socket_only() -> object:
        if ns._connected:
            return 'already connected'
        connect_with_drift_options(ns, ip_clock, False, args)
        return True

    def connect_with_handshake() -> object:
        if ns._connected:
            return 'already connected'
        connect_with_drift_options(ns, ip_clock, True, args)
        return True

    def close() -> object:
        if not ns._connected:
            return 'already disconnected'
        ns.disconnect()
        return True

    def send(cmd: str, data=None) -> Callable[[], object]:
        def action() -> object:
            if ensure_connected():
                return ns.send_command(cmd, data)
            return None
        return action

    def clock_sync_data() -> int:
        return int(time.time() * 1000)

    def ntp_now() -> float:
        return system_to_ntp_time(time.time())

    def shifted_ntp(seconds: float) -> float:
        return system_to_ntp_time(time.time() + seconds)

    def client_clock_start() -> float:
        value = ns.clock_state().get('client_clock_start_ntp')
        if value is None:
            raise RuntimeError('client clock start is unavailable before ntpsync')
        return value

    def send_event_code(event_type: str, label: str = None):
        if len(event_type) != 4:
            raise ValueError('event_code requires exactly 4 characters')
        # This is a diagnostic console: it prints the parsed ECI response
        # for every command, so this one send blocks. Experiments should
        # use the non-blocking default.
        return ns.send_event(
            event_type=event_type,
            label=label or event_type,
            desc='Sent from example2.py experiment',
            wait=True,
        )

    def configure_drift_window_interactive():
        current = ns.drift_estimate()
        min_samples = input(
            'Minimum drift samples '
            f'[{current.get("min_samples")}]: '
        ).strip()
        min_span = input(
            'Minimum drift window seconds '
            f'[{current.get("min_span")}]: '
        ).strip()
        samples = (
            current.get('min_samples') if not min_samples
            else int(min_samples)
        )
        span = current.get('min_span') if not min_span else float(min_span)
        return ns.set_drift_requirements(samples, span)

    def configure_drift_model_interactive():
        current = ns.drift_estimate()
        max_delay = input(
            'Maximum accepted NTP delay seconds '
            f'[{current.get("max_delay")}]: '
        ).strip()
        max_residual = input(
            'Maximum accepted model residual seconds '
            f'[{current.get("max_residual")}]: '
        ).strip()
        window_minutes = input(
            'Rolling model window minutes (0 = all samples) '
            f'[{current.get("window_minutes")}]: '
        ).strip()
        delay = current.get('max_delay') if not max_delay else float(max_delay)
        residual = (
            current.get('max_residual') if not max_residual
            else float(max_residual)
        )
        window = (
            current.get('window_minutes') if not window_minutes
            else float(window_minutes)
        )
        return ns.set_drift_model_options(
            max_delay=delay,
            max_residual=residual,
            window_minutes=window,
        )

    actions = {
        'c': ('Connect socket only', connect_socket_only),
        'h': ('Connect with Query + Attention', connect_with_handshake),
        'q': ('Query', send('Query', args.endian)),
        'y': ('NewQuery', send('NewQuery')),
        'a': ('Attention', send('Attention')),
        't': ('ClockSync', lambda: send('ClockSync', clock_sync_data())()),
        'n': ('raw NTPClockSync', lambda: send('NTPClockSync', ntp_now())()),
        'i': ('high-level ntpsync', lambda: ns.ntpsync(force=True)
              if ensure_connected() else None),
        's': ('sync return clock', lambda: ns.sync_return_clock()
              if ensure_connected() else None),
        'S': ('sync return clock with Attention',
              lambda: ns.sync_return_clock(attention=True)
              if ensure_connected() else None),
        'p': ('raw NTPReturnClock', lambda: send('NTPReturnClock', ntp_now())()),
        'b': ('BeginRecording', send('BeginRecording')),
        'e': ('EndRecording', send('EndRecording')),
        'v': ('EventData simple', send('EventData', make_simple_event())),
        'd': ('EventData with data', send('EventData', make_data_event())),
        'r': ('sample NTP drift', lambda: ns.sample_drift()
              if ensure_connected() else None),
        'g': ('show NTP drift', lambda: print_drift(ns)),
        'R': ('refit drift model', lambda: ns.refresh_drift_model()
              if ensure_connected() else None),
        'G': ('configure drift window',
              lambda: configure_drift_window_interactive()
              if ensure_connected() else None),
        'M': ('configure drift model options',
              lambda: configure_drift_model_interactive()
              if ensure_connected() else None),
        'o': ('show offset history', lambda: print_offsets(ns)),
        'k': ('show clock state', lambda: print_clock_state(ns)),
        'z': ('show session summary', lambda: print_session_summary(ns)),
        'x': ('Exit + close socket', close),
    }

    named_actions = {
        'connect': ('Connect socket only', connect_socket_only),
        'connect_only': ('Connect socket only', connect_socket_only),
        'handshake': ('Connect with Query + Attention', connect_with_handshake),
        'connect_handshake': ('Connect with Query + Attention',
                              connect_with_handshake),
        'query': ('Query', send('Query', args.endian)),
        'new_query': ('NewQuery', send('NewQuery')),
        'attention': ('Attention', send('Attention')),
        'clock_sync': ('ClockSync', lambda: send('ClockSync', clock_sync_data())()),
        'ntp_clock_sync': ('raw NTPClockSync',
                           lambda: send('NTPClockSync', ntp_now())()),
        'ntp_clock_sync_start': ('NTPClockSync with client clock start',
                                 lambda: send('NTPClockSync',
                                              client_clock_start())()),
        'ntpsync': ('high-level ntpsync',
                    lambda: ns.ntpsync(force=True) if ensure_connected() else None),
        'sync_return_clock': ('sync return clock',
                              lambda: ns.sync_return_clock()
                              if ensure_connected() else None),
        'sync_return_clock_attention': ('sync return clock with Attention',
                                        lambda: ns.sync_return_clock(
                                            attention=True
                                        ) if ensure_connected() else None),
        'resync': ('sync return clock',
                   lambda: ns.sync_return_clock()
                   if ensure_connected() else None),
        'resync_attention': ('sync return clock with Attention',
                             lambda: ns.sync_return_clock(attention=True)
                             if ensure_connected() else None),
        'return_clock': ('raw NTPReturnClock',
                         lambda: send('NTPReturnClock', ntp_now())()),
        'return_clock_start': ('NTPReturnClock with client clock start',
                               lambda: send('NTPReturnClock',
                                            client_clock_start())()),
        'begin': ('BeginRecording', send('BeginRecording')),
        'begin_recording': ('BeginRecording', send('BeginRecording')),
        'end': ('EndRecording', send('EndRecording')),
        'end_recording': ('EndRecording', send('EndRecording')),
        'event': ('EventData simple', send('EventData', make_simple_event())),
        'event_data': ('EventData with data',
                       send('EventData', make_data_event())),
        'sample_drift': ('sample NTP drift',
                         lambda: ns.sample_drift()
                         if ensure_connected() else None),
        'drift_sample': ('sample NTP drift',
                         lambda: ns.sample_drift()
                         if ensure_connected() else None),
        'drift': ('show NTP drift', lambda: print_drift(ns)),
        'drift_report': ('show NTP drift', lambda: print_drift(ns)),
        'drift_refit': ('refit drift model',
                        lambda: ns.refresh_drift_model()
                        if ensure_connected() else None),
        'refresh_drift_model': ('refit drift model',
                                lambda: ns.refresh_drift_model()
                                if ensure_connected() else None),
        'drift_on': ('enable drift correction',
                     lambda: ns.set_drift_correction(True)
                     if ensure_connected() else None),
        'drift_off': ('disable drift correction',
                      lambda: ns.set_drift_correction(False)
                      if ensure_connected() else None),
        'drift_model': ('configure drift model options',
                        lambda: configure_drift_model_interactive()
                        if ensure_connected() else None),
        'offsets': ('show offset history', lambda: print_offsets(ns)),
        'clock_state': ('show clock state', lambda: print_clock_state(ns)),
        'session_summary': ('show session summary',
                            lambda: print_session_summary(ns)),
        'close': ('Exit + close socket', close),
        'exit': ('Exit + close socket', close),
    }

    def run_experiment(path: str) -> None:
        print(f'\nRunning experiment: {path}')
        for lineno, command in load_experiment(path):
            parts = command.split()
            name = parts[0].lower()
            label = command
            if name == 'sleep':
                if len(parts) != 2:
                    print(f'Line {lineno}: sleep requires seconds')
                    continue
                try:
                    seconds = float(parts[1])
                except ValueError:
                    print(f'Line {lineno}: invalid sleep seconds: {parts[1]!r}')
                    continue
                run(f'line {lineno}: sleep {seconds}', lambda s=seconds: time.sleep(s))
                continue
            if name == 'ntp_clock_sync_shift':
                if len(parts) != 2:
                    print(f'Line {lineno}: ntp_clock_sync_shift requires seconds')
                    continue
                try:
                    seconds = float(parts[1])
                except ValueError:
                    print(f'Line {lineno}: invalid shift seconds: {parts[1]!r}')
                    continue
                run(
                    f'line {lineno}: NTPClockSync shift {seconds}',
                    lambda s=seconds: send('NTPClockSync', shifted_ntp(s))(),
                )
                continue
            if name == 'event_code':
                if len(parts) < 2:
                    print(f'Line {lineno}: event_code requires a 4-char code')
                    continue
                event_type = parts[1]
                event_label = ' '.join(parts[2:]) if len(parts) > 2 else None
                run(
                    f'line {lineno}: event_code {event_type}',
                    lambda et=event_type, el=event_label: send_event_code(
                        et, el
                    ),
                )
                continue
            if name == 'drift_window':
                if len(parts) != 3:
                    print(
                        f'Line {lineno}: drift_window requires '
                        'min_samples and min_span_seconds'
                    )
                    continue
                try:
                    min_samples = int(parts[1])
                    min_span = float(parts[2])
                except ValueError:
                    print(
                        f'Line {lineno}: invalid drift_window values: '
                        f'{parts[1:]}'
                    )
                    continue
                run(
                    f'line {lineno}: drift_window {min_samples} {min_span}',
                    lambda s=min_samples, w=min_span:
                    ns.set_drift_requirements(s, w)
                    if ensure_connected() else None,
                )
                continue
            if name == 'drift_model':
                if len(parts) not in (3, 4):
                    print(
                        f'Line {lineno}: drift_model requires '
                        'max_delay_seconds, optional max_residual_seconds, '
                        'and window_minutes '
                        '(0 for all samples)'
                    )
                    continue
                try:
                    max_delay = float(parts[1])
                    if len(parts) == 3:
                        max_residual = args.drift_max_residual
                        window_minutes = float(parts[2])
                    else:
                        max_residual = float(parts[2])
                        window_minutes = float(parts[3])
                except ValueError:
                    print(
                        f'Line {lineno}: invalid drift_model values: '
                        f'{parts[1:]}'
                    )
                    continue
                run(
                    f'line {lineno}: drift_model {max_delay} '
                    f'{max_residual} {window_minutes}',
                    lambda d=max_delay, r=max_residual, m=window_minutes:
                    ns.set_drift_model_options(
                        max_delay=d,
                        max_residual=r,
                        window_minutes=m,
                    )
                    if ensure_connected() else None,
                )
                continue
            if name in actions and len(name) == 1:
                step_label, action = actions[name]
            elif name in named_actions:
                step_label, action = named_actions[name]
            else:
                print(f'Line {lineno}: unknown command: {command!r}')
                continue
            run(f'line {lineno}: {label} ({step_label})', action)

    def run_requested_experiment() -> bool:
        if args.experiment:
            run_experiment(args.experiment)
            if args.no_interactive:
                if ns._connected:
                    print('\nClosing socket...')
                    try:
                        ns.disconnect()
                    except Exception as err:
                        print(f'{type(err).__name__}: {_message(err)}')
                print('\nDone.')
                return True
        return False

    if args.transcript:
        Path(args.transcript).parent.mkdir(parents=True, exist_ok=True)
        with open(args.transcript, 'a', encoding='utf-8') as transcript:
            with redirect_stdout(Tee(sys.stdout, transcript)):
                if run_requested_experiment():
                    return
    elif run_requested_experiment():
        return

    try:
        while True:
            print_menu(
                ns._connected, args.mode, ip_cmd, port_cmd, ip_clock,
                args.endian
            )
            key = read_key()
            print(key)
            if key in ('\x03', '\x04'):
                break
            if key == '?':
                continue
            if key not in actions:
                print(f'Unknown key: {key!r}')
                continue
            label, action = actions[key]
            run(label, action)
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        if ns._connected:
            print('\nClosing socket...')
            try:
                ns.disconnect()
            except Exception as err:
                print(f'{type(err).__name__}: {_message(err)}')
        print('\nDone.')


if __name__ == '__main__':
    main()
