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
from typing import Callable

from ntplib import system_to_ntp_time

from egi_pynetstation.NetStation import NetStation
from egi_pynetstation.eci import package_event


def _message(err: Exception) -> str:
    return getattr(err, 'message', str(err))


def print_result(result: object, ns: NetStation = None) -> None:
    if isinstance(result, dict) and not result.get('ok', True):
        label = 'Unexpected response' if result.get('unexpected') else 'ECI response error'
        print(f'{label}: {result.get("error")}: {result.get("message")}')
        print(f'Raw response: {result.get("raw_display")}')
    else:
        print(f'Parsed result: {result!r}')
        if isinstance(result, float) and ns is not None:
            try:
                local_elapsed = ns.getTime()
            except Exception as err:
                print(f'getTime unavailable: {type(err).__name__}: {_message(err)}')
            else:
                print('Timestamp comparison:')
                print(f'  amplifier response: {result:.9f}')
                print(f'  getTime():          {local_elapsed:.9f}')
                print(f'  response-getTime:   {result - local_elapsed:.9f}')


def print_offsets(ns: NetStation) -> None:
    history = ns.clock_offsets()
    if not history:
        print('\nNo clock offset observations yet.')
        return

    print('\nClock offset observations:')
    print('idx  source    local_elapsed_s  amp-package_s')
    t0 = history[0]['local_time']
    for idx, item in enumerate(history):
        elapsed = item['local_time'] - t0
        difference = item.get('difference', item.get('offset'))
        diff_text = 'n/a' if difference is None else f'{difference:> .9f}'
        print(
            f'{idx:>3}  {item["source"]:<8}  '
            f'{elapsed:>15.6f}  {diff_text}'
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
    print('Linear amp-package estimate:')
    print(f'  difference = {intercept:.9f} + {slope:.12f} * elapsed_seconds')
    print(f'  predicted difference now: {predicted_now:.9f} s')


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
    print('s  high-level resync          p  raw NTPReturnClock')
    print('b  BeginRecording             e  EndRecording')
    print('v  EventData simple           d  EventData with data')
    print('o  show offset history        x  Exit + close socket')
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
    args = p.parse_args()

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
        ns.connect(ntp_ip=ip_clock, handshake=False)
        return True

    def connect_with_handshake() -> object:
        if ns._connected:
            return 'already connected'
        ns.connect(ntp_ip=ip_clock, handshake=True)
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

    actions = {
        'c': ('Connect socket only', connect_socket_only),
        'h': ('Connect with Query + Attention', connect_with_handshake),
        'q': ('Query', send('Query', args.endian)),
        'y': ('NewQuery', send('NewQuery')),
        'a': ('Attention', send('Attention')),
        't': ('ClockSync', lambda: send('ClockSync', clock_sync_data())()),
        'n': ('raw NTPClockSync', lambda: send('NTPClockSync', ntp_now())()),
        'i': ('high-level ntpsync', lambda: ns.ntpsync()
              if ensure_connected() else None),
        's': ('high-level resync', lambda: ns.resync()
              if ensure_connected() else None),
        'p': ('raw NTPReturnClock', lambda: send('NTPReturnClock', ntp_now())()),
        'b': ('BeginRecording', send('BeginRecording')),
        'e': ('EndRecording', send('EndRecording')),
        'v': ('EventData simple', send('EventData', make_simple_event())),
        'd': ('EventData with data', send('EventData', make_data_event())),
        'o': ('show offset history', lambda: print_offsets(ns)),
        'x': ('Exit + close socket', close),
    }

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
