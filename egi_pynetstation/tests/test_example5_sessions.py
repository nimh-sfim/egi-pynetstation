"""Command-line semantics for example5's repeated recording sessions."""

from example5_psychopy_photocell_drift import (
    CSV_COLUMNS,
    build_parser,
    build_session_isi_sequence,
    write_frame_intervals,
)


def test_sessions_repeat_the_full_duration_sequence():
    isis, trials_per_session = build_session_isi_sequence(600.0, 2)

    assert len(isis) == trials_per_session * 2
    assert isis[:trials_per_session] == isis[trials_per_session:]
    assert sum(isis[:trials_per_session]) == sum(isis[trials_per_session:])


def test_sessions_option_and_recordings_alias_match():
    parser = build_parser()

    sessions = parser.parse_args([
        'amp', '--sessions', '2', '--duration', '600',
    ])
    recordings = parser.parse_args(['amp', '--recordings', '2'])

    assert sessions.sessions == 2
    assert sessions.duration == 600.0
    assert recordings.sessions == 2


def test_csv_schema_keeps_session_and_model_stage_fields():
    assert {
        'session', 'session_trial', 'recording', 'recording_trial',
        'drift_model_stage', 'active_drift_model_stage',
        'drift_stable_engaged',
    } <= set(CSV_COLUMNS)


def test_frame_interval_log_flags_long_and_missed_frames(tmp_path):
    path = tmp_path / 'frames.csv'
    period = 1.0 / 60.0

    summary = write_frame_intervals(
        str(path), [period, period * 2, 0.333], period,
    )

    assert path.exists()
    assert summary['frames'] == 3
    assert summary['long_frames'] == 2
    assert summary['estimated_missed_frames'] == 20


def test_parser_accepts_frame_interval_log():
    args = build_parser().parse_args([
        'amp', '--frame-interval-log', 'frames.csv',
    ])

    assert args.frame_interval_log == 'frames.csv'
