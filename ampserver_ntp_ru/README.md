# AmpServer NTP Rust Utility

`ampserver_ntp_ru` is a small dependency-free Rust CLI for measuring the
stimulus computer's NTP offset, delay, and jitter against an EGI AmpServer /
Net Station NTP clock.

It only sends UDP NTP requests. It does not connect to the ECI TCP socket and
does not send `NTPClockSync`, `NTPReturnClock`, or event commands.

## Build

```bash
cargo build --release
```

## Example

```bash
cargo run --release -- \
  --server 10.10.10.51 \
  --interval 15 \
  --burst 4 \
  --spacing-ms 50 \
  --duration 3600 \
  --csv ampserver_ntp_noise.csv
```

## Useful Arguments

```text
--server HOST[:PORT]     NTP server, default 10.10.10.51:123
--interval SECONDS       seconds between samples, default 15
--burst N                queries per sample, default 4
--spacing-ms MS          delay between queries inside a burst, default 50
--count N                stop after N samples
--duration SECONDS       stop after this many seconds
--timeout-ms MS          UDP receive timeout, default 1000
--version 3|4            NTP protocol version, default 3
--csv PATH               append CSV samples to PATH
--quiet                  suppress per-sample terminal output
```

The retained sample from each burst is the lowest-delay reply, which is usually
the least noisy offset estimate.
