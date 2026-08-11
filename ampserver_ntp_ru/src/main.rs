use std::env;
use std::fs::{File, OpenOptions};
use std::io::{self, Write};
use std::net::{ToSocketAddrs, UdpSocket};
use std::path::PathBuf;
use std::process;
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const NTP_UNIX_EPOCH_OFFSET: u64 = 2_208_988_800;
const NTP_PACKET_LEN: usize = 48;

#[derive(Debug, Clone, PartialEq)]
struct Config {
    server: String,
    interval: Duration,
    burst: usize,
    spacing: Duration,
    count: Option<usize>,
    duration: Option<Duration>,
    timeout: Duration,
    version: u8,
    csv_path: Option<PathBuf>,
    quiet: bool,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            server: "10.10.10.51:123".to_string(),
            interval: Duration::from_secs(15),
            burst: 4,
            spacing: Duration::from_millis(50),
            count: None,
            duration: None,
            timeout: Duration::from_millis(1_000),
            version: 3,
            csv_path: None,
            quiet: false,
        }
    }
}

#[derive(Debug, Clone)]
struct NtpReply {
    offset: f64,
    delay: f64,
    leap: u8,
    version: u8,
    mode: u8,
    stratum: u8,
    origin_matches: bool,
}

#[derive(Debug, Clone)]
struct BurstSample {
    index: usize,
    elapsed: f64,
    unix_time: f64,
    best: Option<NtpReply>,
    replies: Vec<NtpReply>,
    errors: Vec<String>,
}

fn main() {
    let config = match parse_args(env::args().skip(1)) {
        Ok(config) => config,
        Err(message) => {
            eprintln!("{message}");
            eprintln!();
            print_usage(&mut io::stderr()).ok();
            process::exit(2);
        }
    };

    if let Err(err) = run(config) {
        eprintln!("error: {err}");
        process::exit(1);
    }
}

fn run(config: Config) -> io::Result<()> {
    let addr = config.server.to_socket_addrs()?.next().ok_or_else(|| {
        io::Error::new(io::ErrorKind::InvalidInput, "server resolved to no address")
    })?;

    let socket = UdpSocket::bind("0.0.0.0:0")?;
    socket.set_read_timeout(Some(config.timeout))?;

    let mut csv = match &config.csv_path {
        Some(path) => Some(open_csv(path)?),
        None => None,
    };
    if let Some(file) = csv.as_mut() {
        if file.metadata()?.len() == 0 {
            writeln!(
                file,
                "sample,unix_time,elapsed_s,offset_s,delay_s,burst_replies,burst_errors,stratum,version,mode,leap,origin_matches"
            )?;
        }
    }

    let start = Instant::now();
    let mut sample_index = 0usize;
    loop {
        if let Some(max_count) = config.count {
            if sample_index >= max_count {
                break;
            }
        }
        if let Some(max_duration) = config.duration {
            if start.elapsed() >= max_duration {
                break;
            }
        }

        let sample = collect_burst(
            &socket,
            addr,
            config.version,
            config.burst,
            config.spacing,
            sample_index,
            start,
        );
        emit_sample(&sample, config.quiet, csv.as_mut())?;

        sample_index += 1;
        if config.count == Some(sample_index) {
            break;
        }
        if let Some(max_duration) = config.duration {
            if start.elapsed() >= max_duration {
                break;
            }
        }
        thread::sleep(config.interval);
    }

    Ok(())
}

fn collect_burst(
    socket: &UdpSocket,
    addr: std::net::SocketAddr,
    version: u8,
    burst: usize,
    spacing: Duration,
    sample_index: usize,
    start: Instant,
) -> BurstSample {
    let mut replies = Vec::new();
    let mut errors = Vec::new();

    for query_index in 0..burst.max(1) {
        if query_index > 0 && !spacing.is_zero() {
            thread::sleep(spacing);
        }
        match query_ntp(socket, addr, version) {
            Ok(reply) => replies.push(reply),
            Err(err) => errors.push(format!("{err}")),
        }
    }

    let best = best_reply(&replies).cloned();
    BurstSample {
        index: sample_index,
        elapsed: start.elapsed().as_secs_f64(),
        unix_time: system_time_to_unix_f64(SystemTime::now()),
        best,
        replies,
        errors,
    }
}

fn query_ntp(socket: &UdpSocket, addr: std::net::SocketAddr, version: u8) -> io::Result<NtpReply> {
    let (request, t1_raw, t1) = build_request(SystemTime::now(), version);
    socket.send_to(&request, addr)?;

    let mut response = [0u8; NTP_PACKET_LEN];
    let (len, _) = socket.recv_from(&mut response)?;
    let t4 = system_time_to_ntp_f64(SystemTime::now());
    if len < NTP_PACKET_LEN {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!("short NTP response: {len} bytes"),
        ));
    }

    parse_reply(&response, t1_raw, t1, t4)
}

fn build_request(now: SystemTime, version: u8) -> ([u8; NTP_PACKET_LEN], u64, f64) {
    let mut packet = [0u8; NTP_PACKET_LEN];
    packet[0] = (version << 3) | 3;
    let raw = system_time_to_ntp_raw(now);
    packet[40..48].copy_from_slice(&raw.to_be_bytes());
    (packet, raw, ntp_raw_to_f64(raw))
}

fn parse_reply(
    packet: &[u8; NTP_PACKET_LEN],
    client_transmit_raw: u64,
    t1: f64,
    t4: f64,
) -> io::Result<NtpReply> {
    let flags = packet[0];
    let leap = flags >> 6;
    let version = (flags >> 3) & 0b111;
    let mode = flags & 0b111;
    let stratum = packet[1];
    let originate_raw = read_timestamp(packet, 24);
    let receive_raw = read_timestamp(packet, 32);
    let transmit_raw = read_timestamp(packet, 40);

    if mode != 4 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!("response mode is {mode}, expected server mode 4"),
        ));
    }
    if stratum == 0 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "kiss-of-death or invalid stratum 0 response",
        ));
    }

    let t2 = ntp_raw_to_f64(receive_raw);
    let t3 = ntp_raw_to_f64(transmit_raw);
    let delay = (t4 - t1) - (t3 - t2);
    let offset = ((t2 - t1) + (t3 - t4)) / 2.0;

    Ok(NtpReply {
        offset,
        delay,
        leap,
        version,
        mode,
        stratum,
        origin_matches: originate_raw == client_transmit_raw,
    })
}

fn best_reply(replies: &[NtpReply]) -> Option<&NtpReply> {
    replies
        .iter()
        .filter(|reply| reply.delay.is_finite() && reply.delay >= 0.0)
        .min_by(|a, b| a.delay.total_cmp(&b.delay))
        .or_else(|| replies.iter().min_by(|a, b| a.delay.total_cmp(&b.delay)))
}

fn emit_sample(sample: &BurstSample, quiet: bool, csv: Option<&mut File>) -> io::Result<()> {
    if let Some(best) = &sample.best {
        if !quiet {
            println!(
                "#{:<5} elapsed={:>9.3}s offset={:>+11.6}ms delay={:>9.6}ms replies={} errors={} stratum={}{}",
                sample.index,
                sample.elapsed,
                best.offset * 1_000.0,
                best.delay * 1_000.0,
                sample.replies.len(),
                sample.errors.len(),
                best.stratum,
                if best.origin_matches { "" } else { " origin_mismatch" },
            );
        }
        if let Some(file) = csv {
            writeln!(
                file,
                "{},{:.9},{:.9},{:.12},{:.12},{},{},{},{},{},{},{}",
                sample.index,
                sample.unix_time,
                sample.elapsed,
                best.offset,
                best.delay,
                sample.replies.len(),
                sample.errors.len(),
                best.stratum,
                best.version,
                best.mode,
                best.leap,
                best.origin_matches,
            )?;
        }
    } else {
        if !quiet {
            println!(
                "#{:<5} elapsed={:>9.3}s no_reply errors={}",
                sample.index,
                sample.elapsed,
                sample.errors.len()
            );
        }
        if let Some(file) = csv {
            writeln!(
                file,
                "{},{:.9},{:.9},,,,,{},,,,,",
                sample.index,
                sample.unix_time,
                sample.elapsed,
                sample.errors.len(),
            )?;
        }
    }
    Ok(())
}

fn open_csv(path: &PathBuf) -> io::Result<File> {
    OpenOptions::new().create(true).append(true).open(path)
}

fn parse_args<I>(args: I) -> Result<Config, String>
where
    I: IntoIterator<Item = String>,
{
    let mut config = Config::default();
    let mut args = args.into_iter();
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "-h" | "--help" => {
                print_usage(&mut io::stdout()).map_err(|err| err.to_string())?;
                process::exit(0);
            }
            "--server" => {
                config.server = normalize_server(&take_value(&mut args, "--server")?);
            }
            "--interval" => {
                config.interval =
                    parse_duration_secs(&take_value(&mut args, "--interval")?, "--interval")?;
            }
            "--burst" => {
                config.burst = parse_usize(&take_value(&mut args, "--burst")?, "--burst")?;
                if config.burst == 0 {
                    return Err("--burst must be at least 1".to_string());
                }
            }
            "--spacing-ms" => {
                config.spacing =
                    parse_duration_ms(&take_value(&mut args, "--spacing-ms")?, "--spacing-ms")?;
            }
            "--count" => {
                config.count = Some(parse_usize(&take_value(&mut args, "--count")?, "--count")?);
            }
            "--duration" => {
                config.duration = Some(parse_duration_secs(
                    &take_value(&mut args, "--duration")?,
                    "--duration",
                )?);
            }
            "--timeout-ms" => {
                config.timeout =
                    parse_duration_ms(&take_value(&mut args, "--timeout-ms")?, "--timeout-ms")?;
            }
            "--version" => {
                let version = parse_u8(&take_value(&mut args, "--version")?, "--version")?;
                if version != 3 && version != 4 {
                    return Err("--version must be 3 or 4".to_string());
                }
                config.version = version;
            }
            "--csv" => {
                config.csv_path = Some(PathBuf::from(take_value(&mut args, "--csv")?));
            }
            "--quiet" => {
                config.quiet = true;
            }
            _ => return Err(format!("unknown argument: {arg}")),
        }
    }
    Ok(config)
}

fn normalize_server(server: &str) -> String {
    if server.contains(':') {
        server.to_string()
    } else {
        format!("{server}:123")
    }
}

fn take_value<I>(args: &mut I, flag: &str) -> Result<String, String>
where
    I: Iterator<Item = String>,
{
    args.next()
        .ok_or_else(|| format!("{flag} requires a value"))
}

fn parse_duration_secs(value: &str, flag: &str) -> Result<Duration, String> {
    let secs = value
        .parse::<f64>()
        .map_err(|_| format!("{flag} must be a number of seconds"))?;
    if !secs.is_finite() || secs < 0.0 {
        return Err(format!("{flag} must be non-negative"));
    }
    Ok(Duration::from_secs_f64(secs))
}

fn parse_duration_ms(value: &str, flag: &str) -> Result<Duration, String> {
    let ms = value
        .parse::<f64>()
        .map_err(|_| format!("{flag} must be a number of milliseconds"))?;
    if !ms.is_finite() || ms < 0.0 {
        return Err(format!("{flag} must be non-negative"));
    }
    Ok(Duration::from_secs_f64(ms / 1_000.0))
}

fn parse_usize(value: &str, flag: &str) -> Result<usize, String> {
    value
        .parse::<usize>()
        .map_err(|_| format!("{flag} must be a positive integer"))
}

fn parse_u8(value: &str, flag: &str) -> Result<u8, String> {
    value
        .parse::<u8>()
        .map_err(|_| format!("{flag} must be an integer"))
}

fn print_usage(out: &mut dyn Write) -> io::Result<()> {
    writeln!(
        out,
        "Usage: ampserver_ntp_ru [OPTIONS]\n\
\n\
Options:\n\
  --server HOST[:PORT]   NTP server [default: 10.10.10.51:123]\n\
  --interval SECONDS     Seconds between retained samples [default: 15]\n\
  --burst N              NTP queries per sample; lowest-delay reply wins [default: 4]\n\
  --spacing-ms MS        Milliseconds between burst queries [default: 50]\n\
  --count N              Stop after N retained samples\n\
  --duration SECONDS     Stop after this many seconds\n\
  --timeout-ms MS        UDP receive timeout [default: 1000]\n\
  --version 3|4          NTP protocol version [default: 3]\n\
  --csv PATH             Append retained samples to a CSV file\n\
  --quiet                Do not print per-sample output\n\
  -h, --help             Show this help"
    )
}

fn system_time_to_unix_f64(time: SystemTime) -> f64 {
    let duration = time
        .duration_since(UNIX_EPOCH)
        .expect("system clock is before the Unix epoch");
    duration.as_secs() as f64 + f64::from(duration.subsec_nanos()) / 1_000_000_000.0
}

fn system_time_to_ntp_f64(time: SystemTime) -> f64 {
    system_time_to_unix_f64(time) + NTP_UNIX_EPOCH_OFFSET as f64
}

fn system_time_to_ntp_raw(time: SystemTime) -> u64 {
    let duration = time
        .duration_since(UNIX_EPOCH)
        .expect("system clock is before the Unix epoch");
    let secs = duration.as_secs() + NTP_UNIX_EPOCH_OFFSET;
    let frac = ((u64::from(duration.subsec_nanos())) << 32) / 1_000_000_000u64;
    (secs << 32) | frac
}

fn ntp_raw_to_f64(raw: u64) -> f64 {
    let secs = raw >> 32;
    let frac = raw & 0xffff_ffff;
    secs as f64 + frac as f64 / 4_294_967_296.0
}

fn read_timestamp(packet: &[u8; NTP_PACKET_LEN], start: usize) -> u64 {
    let mut bytes = [0u8; 8];
    bytes.copy_from_slice(&packet[start..start + 8]);
    u64::from_be_bytes(bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_server_without_port_to_ntp_port() {
        assert_eq!(normalize_server("10.10.10.51"), "10.10.10.51:123");
        assert_eq!(normalize_server("10.10.10.51:8123"), "10.10.10.51:8123");
    }

    #[test]
    fn parses_useful_cli_arguments() {
        let config = parse_args(
            [
                "--server",
                "10.10.10.51",
                "--interval",
                "2.5",
                "--burst",
                "3",
                "--spacing-ms",
                "25",
                "--count",
                "10",
                "--timeout-ms",
                "250",
                "--version",
                "4",
                "--csv",
                "noise.csv",
                "--quiet",
            ]
            .into_iter()
            .map(String::from),
        )
        .unwrap();

        assert_eq!(config.server, "10.10.10.51:123");
        assert_eq!(config.interval, Duration::from_millis(2_500));
        assert_eq!(config.burst, 3);
        assert_eq!(config.spacing, Duration::from_millis(25));
        assert_eq!(config.count, Some(10));
        assert_eq!(config.timeout, Duration::from_millis(250));
        assert_eq!(config.version, 4);
        assert_eq!(config.csv_path, Some(PathBuf::from("noise.csv")));
        assert!(config.quiet);
    }

    #[test]
    fn rejects_zero_burst_and_unknown_version() {
        assert!(parse_args(["--burst", "0"].into_iter().map(String::from)).is_err());
        assert!(parse_args(["--version", "5"].into_iter().map(String::from)).is_err());
    }

    #[test]
    fn encodes_ntp_request_version_and_transmit_timestamp() {
        let now = UNIX_EPOCH + Duration::new(1, 500_000_000);
        let (packet, raw, t1) = build_request(now, 3);

        assert_eq!(packet[0], 0b0001_1011);
        assert_eq!(raw, ((NTP_UNIX_EPOCH_OFFSET + 1) << 32) | (1u64 << 31));
        assert_eq!(read_timestamp(&packet, 40), raw);
        assert!((t1 - (NTP_UNIX_EPOCH_OFFSET as f64 + 1.5)).abs() < 1e-9);
    }

    #[test]
    fn parses_reply_offset_delay_and_origin_match() {
        let t1_raw = f64_to_ntp_raw_for_test(1000.0);
        let t2_raw = f64_to_ntp_raw_for_test(1000.010);
        let t3_raw = f64_to_ntp_raw_for_test(1000.012);
        let t4 = 1000.030;
        let mut packet = [0u8; NTP_PACKET_LEN];
        packet[0] = (3 << 3) | 4;
        packet[1] = 1;
        packet[24..32].copy_from_slice(&t1_raw.to_be_bytes());
        packet[32..40].copy_from_slice(&t2_raw.to_be_bytes());
        packet[40..48].copy_from_slice(&t3_raw.to_be_bytes());

        let reply = parse_reply(&packet, t1_raw, 1000.0, t4).unwrap();

        assert!((reply.offset - -0.004).abs() < 1e-9);
        assert!((reply.delay - 0.028).abs() < 1e-9);
        assert!(reply.origin_matches);
        assert_eq!(reply.mode, 4);
        assert_eq!(reply.version, 3);
    }

    #[test]
    fn chooses_lowest_non_negative_delay_reply() {
        let slow = NtpReply {
            offset: 0.001,
            delay: 0.010,
            leap: 0,
            version: 3,
            mode: 4,
            stratum: 1,
            origin_matches: true,
        };
        let fast = NtpReply {
            offset: 0.002,
            delay: 0.003,
            ..slow.clone()
        };

        let replies = vec![slow, fast.clone()];
        assert_eq!(best_reply(&replies).unwrap().offset, fast.offset);
    }

    #[test]
    fn rejects_non_server_and_stratum_zero_replies() {
        let mut packet = [0u8; NTP_PACKET_LEN];
        packet[0] = (3 << 3) | 3;
        packet[1] = 1;
        assert!(parse_reply(&packet, 0, 0.0, 0.0).is_err());

        packet[0] = (3 << 3) | 4;
        packet[1] = 0;
        assert!(parse_reply(&packet, 0, 0.0, 0.0).is_err());
    }

    fn f64_to_ntp_raw_for_test(value: f64) -> u64 {
        let secs = value.floor() as u64;
        let frac = ((value - secs as f64) * 4_294_967_296.0).round() as u64;
        (secs << 32) | frac
    }
}
