# Contributing to egi-pynetstation

Thank you for helping improve `egi-pynetstation`. Contributions that improve
ECI compatibility, experimental timing, documentation, tests, and platform
support are especially useful.

## Getting started

1. Open an issue before starting a substantial change so the approach can be
   discussed publicly.
2. Fork the repository and create a focused branch from `main`.
3. Install the package and test dependencies in an isolated Python environment:

   ```bash
   python -m pip install -e .
   python -m pip install pytest flake8 sphinx sphinx-rtd-theme
   ```

4. Run the tests with `pytest` from the repository root. Build the documentation
   with `make html` from `docs/`.

## Pull requests

Keep each pull request focused on one change, explain its motivation, and add
or update tests for behavior changes. Do not include private participant data,
institutional network addresses, or other sensitive experimental material.

Changes affecting event timestamps, NTP sampling, or asynchronous event
delivery need particular care. Describe how timing was assessed, retain the
monotonic-clock assumptions where applicable, and avoid introducing blocking
work on a stimulus presentation or display-flip thread.

### Adding a drift option

`connect()` collects its drift settings into a `drift_options` dict and hands
that to `_configure_and_handshake()`, which reads each value by name. Adding a
setting means touching four places: the `connect()` signature and its
docstring, the dict, `NetStation._DRIFT_OPTION_KEYS`, and the setter call that
consumes it. `_configure_and_handshake()` rejects unknown and missing keys, so
a typo or an omission fails at connect time rather than silently leaving the
setting at its default.

Two rules to preserve. Pass values through untouched — `None` means "leave
unchanged" at every setter, and the conditionals that used to gate these calls
caused three separate silent-config bugs. And extend
`test_every_drift_option_reaches_its_setter` with a value distinct from both
the default and the other options, which is what catches a transposition.

Please update the relevant README and Sphinx documentation when changing a
public API or a recommended experimental workflow. By contributing, you agree
that your contributions may be distributed under the project's license.

## Reporting issues

Use the issue tracker for reproducible bugs and feature requests. For a timing
or hardware issue, include the package and Python versions, operating system,
Net Station or amplifier version when available, a minimal code example, and
the result of `python -m egi_pynetstation.check_clocks`. Do not post recordings
or participant data.
