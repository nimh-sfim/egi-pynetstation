# Changelog

All notable changes to `egi-pynetstation` are recorded here. This project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added

- Optional staged drift correction: a provisional short-baseline model can
  sample faster and correct startup drift until the ordinary long-baseline
  model is accepted, at which point the stable model permanently takes over.
- Multiple recordings may now share one connection. Each `begin_rec()`
  performs its own NTP sync while retained drift samples are translated into
  the new elapsed-time origin and correction is re-anchored continuously.
- The PsychoPy photocell example can log every frame interval with long-frame
  and estimated missed-refresh flags via `--frame-interval-log`. Each frame
  row now also carries `psychopy_time_s` and `package_time_s`, the frame's
  absolute time on the same clocks the offset log and drift JSON-lines log
  use, so frame drops can be joined directly against drift records.
- The PsychoPy photocell example accepts `--session-break` to keep its
  black window flipping between consecutive Net Station recordings, allowing
  recording-boundary display settling to be tested without dropping the ECI
  connection.
- Post-engagement drift monitoring, tunable via `set_drift_monitoring()` (and
  the example's `--drift-excursion-threshold`, `--drift-sample-reject-offset`,
  and `--drift-status-interval`): a `drift_level_excursion`/
  `drift_level_recovered` pair when a measured offset leaves and returns to
  the engaged model, a `drift_sample_rejected` record for a multi-second
  sample dropped before it can corrupt a fit, and a periodic
  `drift_model_status` heartbeat so a quiet log still carries model state.

### Changed

- Reorganized the documentation around a short five-command quickstart and a
  required per-setup Timing Test, with optional drift warmup, tuning, timing
  internals, diagnostics, and platform details separated from the basic API.

### Fixed

- The photocell CSV schema now retains session identifiers and staged-model
  fields instead of silently dropping them as extra dictionary keys.
- Drift-model status heartbeats now keep their schedule when a second
  recording starts on the same connection. Previously the heartbeat retained
  the first recording's elapsed-time coordinate and could remain silent for
  the whole second recording.

## [2.1.0] — 2026-08-28

Event timing on Windows was quantized to the system timer tick, and drift
correction silently never engaged there. Both are fixed. If you record on
Windows with Python before 3.13, upgrade.

### Fixed

- **Windows event timestamps were quantized to ~15.6 ms, and drift
  correction never engaged.** Before Python 3.13, CPython implements
  `time.monotonic()` with `GetTickCount64()` and `time.time()` with
  `GetSystemTimeAsFileTime()`, both of which advance on the system timer
  tick. That granularity entered every NTP offset sample twice, so no
  fitted line could satisfy `drift_max_residual` and every fit was
  rejected — with no error raised. A one-hour photocell validation on the
  released 2.0.0 code drifted 172 ms with no correction ever applied; the
  same recording on 2.1.0 held to a 3 ms range. The package now reads
  `QueryPerformanceCounter` and `GetSystemTimePreciseAsFileTime` directly
  rather than relying on interpreter defaults.
- The `drift_model_engaged` log record could be written while the model had
  not in fact been activated. Fit acceptance and model activation were
  separate steps, and the record was emitted for the former. They are now
  one operation, and the record reports `drift_accepted_fits: 1`.

### Added

- `capture_time()` and `time_at_capture()` — capture the package's
  high-resolution clock on the critical path and convert it afterwards.
  Prefer these over `time_at_monotonic()`.
- `clock_report()` — which clocks this process uses and their *measured*
  resolution. `connect()` calls it automatically and stores the result in
  the `session_start` log record, so every recording carries the clock
  provenance of the machine that produced it. `connect()` also raises a
  `RuntimeWarning` if either timing clock measures coarser than 1 ms.
- `drift_ready()` — whether drift correction can be trusted at this
  instant, with a `reason` when it cannot. Unlike `session_summary()['ok']`
  this reports `settling` while a newly accepted fit's level error is still
  being slewed in, an interval during which timestamps are knowingly
  incomplete.
- `wait_for_drift()` — block until ready, with an `on_wait` callback for
  drawing a progress display. Timing out is not an error: it returns the
  verdict so the caller can decide whether to proceed.
- `connect(drift_presync=...)` and `set_drift_sampling(presync=...)` — see
  Changed.
- NTP replies are validated before they can anchor a recording: mode, leap
  indicator, stratum, and a match between the reply's originate timestamp
  and the request.

### Changed

- **Drift samples taken between `connect()` and `begin_rec()` are now kept**
  (`drift_presync`, default `True`). A fitted slope is invariant under a
  shift of the time origin, so those samples are given their elapsed
  coordinate when `ntpsync()` establishes the epoch. An experiment that
  calls `connect()` at the start of cap application can reach its first
  trial with the model already engaged, at no cost in recorded time. Pass
  `drift_presync=False` for the previous behaviour.
- `sample_drift()` **no longer raises before the clock sync.** It returns a
  sample with `elapsed=None`, backfilled at `ntpsync()`. With
  `drift_presync=False` it raises as before.
- `ntpsync()`, and therefore `begin_rec()`, can now raise `NTPException`
  for a reply that fails validation, where 2.0.0 would have proceeded.
- `time_at_capture()` raises rather than silently falling back to the wall
  clock when no capture-clock anchor exists.
- `ntplib` is no longer a dependency. The client is vendored as
  `egi_pynetstation.egi_ntp`, a fork of ntplib 0.4.0 carrying the clock
  changes above and the reply validation. The public helper API is
  unchanged.

### Deprecated

- `time_at_monotonic()`, to be removed in 3.0. Use `capture_time()` with
  `time_at_capture()`. On Windows before Python 3.13 this method cannot be
  made accurate — `time.monotonic()` quantizes the caller's reading before
  the package ever sees it — so its first use there emits a `FutureWarning`.

## [2.0.0]

Drift-corrected event timestamps, the asynchronous event sender, structured
error logging, and the `check_clocks` diagnostic. See the repository history
for detail.

[2.1.0]: https://github.com/nimh-sfim/egi-pynetstation/releases/tag/v2.1.0
[2.0.0]: https://github.com/nimh-sfim/egi-pynetstation/releases/tag/v2.0.0
