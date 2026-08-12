#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""GUI launcher for example5_psychopy_photocell_drift.py.

Open this file in PsychoPy Coder/Runner and press Run. A startup dialog will
collect the common options, then the regular command-line script is launched
with those settings.

The dialog is built from a single FIELDS table below, which also carries the
command-line option each value maps to. Previously the defaults dictionary
and the addField() calls were separate lists whose order had to match
exactly, so adding one field in the wrong place silently shifted every
later value onto the wrong setting.
"""

from datetime import datetime
from pathlib import Path

from psychopy import gui

import example5_psychopy_photocell_drift as photocell


def default_log_paths() -> tuple:
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_dir = Path.cwd() / 'logs'
    return (
        str(log_dir / f'psychopy_photocell_{stamp}.csv'),
        str(log_dir / f'psychopy_photocell_errors_{stamp}.jsonl'),
    )


CSV_LOG, ERROR_LOG = default_log_paths()

# (key, label, default, option, kind)
#   kind 'value'  -> passed as "--option value" when not blank
#   kind 'flag'   -> passed as "--option" when true
#   kind 'choice' -> like 'value', with a fixed set of options
#   option None   -> handled specially in dialog_values_to_argv()
FIELDS = [
    ('__section__', 'Network', None, None, 'section'),
    ('mode', 'Mode', 'amp', None, 'choice'),
    ('ip_cmd', 'Net Station IP', '', '--ip-cmd', 'value'),
    ('ip_clock', 'NTP / amp IP', '', '--ip-clock', 'value'),
    ('port', 'ECI port', '', '--port', 'value'),

    ('__section__', 'Timing', None, None, 'section'),
    ('duration_s', 'Duration (s)', 300.0, '--duration', 'value'),
    ('dot_duration_s', 'Dot duration (s)', 0.100, '--dot-duration', 'value'),
    ('dot_radius', 'Dot radius', 0.045, '--dot-radius', 'value'),
    ('dot_x', 'Dot X', 0.72, None, 'value'),
    ('dot_y', 'Dot Y', 0.40, None, 'value'),

    ('__section__', 'Drift correction', None, None, 'section'),
    ('sample_interval_s', 'NTP sample interval (s)', 15.0,
     '--sample-interval', 'value'),
    ('drift_min_samples', 'Drift min samples', 13,
     '--drift-min-samples', 'value'),
    ('drift_min_span_s', 'Drift min span (s)', 180.0,
     '--drift-min-span', 'value'),
    ('drift_max_delay_s', 'Reject NTP delay above (s)', 0.010,
     '--drift-max-delay', 'value'),
    ('drift_max_residual_s', 'Reject drift fit residual above (s)', 0.003,
     '--drift-max-residual', 'value'),
    ('drift_window_min', 'Keep last N minutes (0 = all)', 15.0,
     '--drift-window-minutes', 'value'),
    ('disable_drift_correction', 'Disable drift correction', False,
     '--no-drift-correction', 'flag'),

    ('__section__', 'NTP sampling', None, None, 'section'),
    ('drift_samples', 'NTP queries per sample', 4,
     '--drift-samples', 'value'),
    ('drift_sample_spacing_s', 'Seconds between queries in a burst', 0.05,
     '--drift-sample-spacing', 'value'),
    ('drift_min_pause_s', 'Minimum ITI to sample in (s)', 0.35,
     '--drift-min-pause', 'value'),
    ('drift_background', 'Sample from a background thread', False,
     '--drift-background', 'flag'),

    ('__section__', 'Drift model stability', None, None, 'section'),
    ('drift_slew', 'Max level correction rate (s per s)', 0.0002,
     '--drift-slew', 'value'),
    ('drift_max_model_age_s', 'Stop extrapolating after (s, 0 = never)',
     600.0, '--drift-max-model-age', 'value'),
    ('drift_stall_after', 'Rejected fits before logging a stall', 5,
     '--drift-stall-after', 'value'),

    ('__section__', 'Diagnostics', None, None, 'section'),
    ('sync_events', 'Send events synchronously (slower; for comparison)',
     False, '--sync-events', 'flag'),
    ('ntpsync_before_every', 'Sync before every N dots (0=off)', 0,
     '--ntpsync-every', 'value'),
    ('ntpsync_after_every', 'Sync after every N dots (0=off)', 0,
     '--ntpsync-after-every', 'value'),

    ('__section__', 'Display', None, None, 'section'),
    ('fullscreen', 'Fullscreen', True, '--fullscreen', 'flag'),
    ('screen', 'Screen index', 0, '--screen', 'value'),

    ('__section__', 'Logging', None, None, 'section'),
    ('debug_eci', 'Debug ECI traffic', False, '--debug', 'flag'),
    ('csv_log', 'CSV log path', CSV_LOG, '--log', 'value'),
    ('error_log', 'Error log path', ERROR_LOG, '--error-log', 'value'),
]

MODE_CHOICES = ['amp', 'local', 'custom']


def input_fields() -> list:
    """Every real field, in dialog order, excluding section headings."""
    return [f for f in FIELDS if f[4] != 'section']


def build_dialog_values() -> dict:
    return {key: default for key, _, default, _, kind in FIELDS
            if kind != 'section'}


def show_startup_dialog() -> dict:
    dialog = gui.Dlg(title='EGI PyNetStation Photocell Drift Test')
    for key, label, default, _option, kind in FIELDS:
        if kind == 'section':
            dialog.addText(label)
        elif kind == 'choice':
            dialog.addField(label, default, choices=MODE_CHOICES)
        else:
            dialog.addField(label, default)

    result = dialog.show()
    if not dialog.OK:
        return None

    # Order is guaranteed because both the dialog and this mapping are
    # built from FIELDS in the same pass.
    return dict(zip([f[0] for f in input_fields()], result))


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
    for key, _label, _default, option, kind in input_fields():
        if option is None:
            continue          # 'mode' and the dot position, handled below
        if kind == 'flag':
            if as_bool(values[key]):
                argv.append(option)
        else:
            add_option(argv, option, values[key])
    argv.extend(['--dot-pos', str(values['dot_x']), str(values['dot_y'])])
    return argv


def main() -> int:
    values = show_startup_dialog()
    if values is None:
        return 0
    argv = dialog_values_to_argv(values)
    return photocell.main(argv)


if __name__ == '__main__':
    raise SystemExit(main())
