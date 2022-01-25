from egi_pynetstation.NetStation import NetStation
from time import time

from argparse import ArgumentParser


def high_res_sleep(amount: float):
    """Uses repeated time interrogations to try and sleep precisely"""
    t0 = time()
    while (time() - t0) < amount:
        continue


def namer(x: int) -> str:
    return 't %2.2d' % x


def main():
    p = ArgumentParser(description="Demonstrate NetStation Interface")
    p.add_argument('mode', choices=['local', 'amp'])
    args = p.parse_args()

    # Local mode designed to work with AmpServer Testing Applications
    # Amp mode for working with the actual EGI Amplifier
    # If you have the amplifier, you probably want 'amp' mode
    # The _cmd is what you're sending commands to in python (like NetStation)
    # the _clock is the virtualized amplifier
    if args.mode == 'local':
        IP_cmd = '127.0.0.1'
        IP_clock = '216.239.35.4'
        port_cmd = 9885

        eci_client = NetStation(IP_cmd, port_cmd)
        eci_client.connect(ntp_ip=IP_clock)
    elif args.mode == 'amp':
        IP_ns = '10.10.10.42' # IP Address of Net Station
        IP_amp = '10.10.10.51' # IP Address of Amplifier
        port_ns = 55513 #Port configured for ECI in Net Station

        eci_client = NetStation(IP_ns, port_ns)
        eci_client.connect(ntp_ip=IP_amp)
    else:
        raise RuntimeError('Something strange has occured')


    eci_client.begin_rec()
    eci_client.send_event(event_type="STRT", start=0.0)

    for i in range(10):
        high_res_sleep(3)
        name = namer(i)
        eci_client.send_event(event_type=name)
        if (i % 4) == 0:
            eci_client.resync()

    eci_client.end_rec()
    eci_client.disconnect()


if __name__ == '__main__':
    main()
