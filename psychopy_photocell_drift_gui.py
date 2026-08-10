#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""GUI launcher for psychopy_photocell_drift.py.

Open this file in PsychoPy Coder/Runner and press Run. A startup dialog will
collect the common options, then the regular command-line script is launched
with those settings.
"""

from datetime import datetime
from pathlib import Path

from psychopy import gui

import psychopy_photocell_drift


def default_log_paths() -> tuple:
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_dir = Path.cwd() / 'logs'
    return (
        str(log_dir / f'psychopy_photocell_{stamp}.csv'),
        str(log_dir / f'psychopy_photocell_errors_{stamp}.jsonl'),
    )


def build_dialog_values() -> dict:
    log_path, error_log_path = default_log_paths()
    return {
        'mode': 'amp',
        'ip_cmd': '',
        'ip_clock': '',
        'port': '',
        'duration_s': 300.0,
        'dot_duration_s': 0.100,
        'dot_radius': 0.045,
        'dot_x': 0.72,
        'dot_y': 0.40,
        'sample_interval_s': 15.0,
        'drift_min_samples': 13,
        'drift_min_span_s': 180.0,
        'drift_max_delay_s': 0.010,
        'drift_max_residual_s': 0.003,
        'drift_window_min': 15.0,
        'disable_drift_correction': False,
        'ntpsync_before_every': 0,
        'ntpsync_after_every': 0,
        'fullscreen': True,
        'screen': 0,
        'debug_eci': False,
        'csv_log': log_path,
        'error_log': error_log_path,
    }


def show_startup_dialog() -> dict:
    values = build_dialog_values()
    dialog = gui.Dlg(title='EGI PyNetStation Photocell Drift Test')
    dialog.addText('Network')
    dialog.addField('Mode', values['mode'], choices=['amp', 'local', 'custom'])
    dialog.addField('Net Station IP', values['ip_cmd'])
    dialog.addField('NTP / amp IP', values['ip_clock'])
    dialog.addField('ECI port', values['port'])

    dialog.addText('Timing')
    dialog.addField('Duration (s)', values['duration_s'])
    dialog.addField('Dot duration (s)', values['dot_duration_s'])
    dialog.addField('Dot radius', values['dot_radius'])
    dialog.addField('Dot X', values['dot_x'])
    dialog.addField('Dot Y', values['dot_y'])
    dialog.addField('NTP sample interval (s)', values['sample_interval_s'])

    dialog.addText('Drift correction')
    dialog.addField('Drift min samples', values['drift_min_samples'])
    dialog.addField('Drift min span (s)', values['drift_min_span_s'])
    dialog.addField('Reject NTP delay above (s)', values['drift_max_delay_s'])
    dialog.addField('Reject drift fit residual above (s)',
                    values['drift_max_residual_s'])
    dialog.addField('Keep last N minutes (0 = all)', values['drift_window_min'])
    dialog.addField('Disable drift correction', values['disable_drift_correction'])

    dialog.addText('Diagnostic repeated ECI ntpsync')
    dialog.addField('Sync before every N dots (0=off)',
                    values['ntpsync_before_every'])
    dialog.addField('Sync after every N dots (0=off)',
                    values['ntpsync_after_every'])

    dialog.addText('Display')
    dialog.addField('Fullscreen', values['fullscreen'])
    dialog.addField('Screen index', values['screen'])

    dialog.addText('Logging')
    dialog.addField('Debug ECI traffic', values['debug_eci'])
    dialog.addField('CSV log path', values['csv_log'])
    dialog.addField('Error log path', values['error_log'])

    result = dialog.show()
    if not dialog.OK:
        return None

    keys = list(values)
    return dict(zip(keys, result))


def add_option(args: list, option: str, value) -> None:
    if value not in ('', None):
        args.extend([option, str(value)])


def as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ('true', '1', 'yes', 'y')
    return bool(value)


def dialog_values_to_argv(values: dict) -> list:
    argv = [values['mode']]
    add_option(argv, '--ip-cmd', values['ip_cmd'])
    add_option(argv, '--ip-clock', values['ip_clock'])
    add_option(argv, '--port', values['port'])
    add_option(argv, '--duration', values['duration_s'])
    add_option(argv, '--dot-duration', values['dot_duration_s'])
    add_option(argv, '--dot-radius', values['dot_radius'])
    argv.extend(['--dot-pos', str(values['dot_x']), str(values['dot_y'])])
    add_option(argv, '--sample-interval', values['sample_interval_s'])
    add_option(argv, '--drift-min-samples', values['drift_min_samples'])
    add_option(argv, '--drift-min-span', values['drift_min_span_s'])
    add_option(argv, '--drift-max-delay', values['drift_max_delay_s'])
    add_option(argv, '--drift-max-residual', values['drift_max_residual_s'])
    add_option(argv, '--drift-window-minutes', values['drift_window_min'])
    add_option(argv, '--ntpsync-every', values['ntpsync_before_every'])
    add_option(argv, '--ntpsync-after-every', values['ntpsync_after_every'])
    add_option(argv, '--screen', values['screen'])
    add_option(argv, '--log', values['csv_log'])
    add_option(argv, '--error-log', values['error_log'])

    if as_bool(values['disable_drift_correction']):
        argv.append('--no-drift-correction')
    if as_bool(values['fullscreen']):
        argv.append('--fullscreen')
    if as_bool(values['debug_eci']):
        argv.append('--debug')
    return argv


def main() -> int:
    values = show_startup_dialog()
    if values is None:
        return 0
    argv = dialog_values_to_argv(values)
    return psychopy_photocell_drift.main(argv)


if __name__ == '__main__':
    raise SystemExit(main())
