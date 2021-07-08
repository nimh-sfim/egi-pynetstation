from eci.NetStation import NetStation
from eci.eci import package_event
from time import sleep

from argparse import ArgumentParser

def main():
    p = ArgumentParser(description="Demonstrate NetStation Interface")
    p.add_argument('mode', choices=['local', 'amp'])
    args = p.parse_args()

    if args.mode == 'local':
        IP = '127.0.0.1'
        IP_amp = '216.239.35.4'
        port = 9885
    elif args.mode == 'amp':
        IP = '10.10.10.42'
        IP_amp = '10.10.10.51'
        port = 55513
    else:
        raise RuntimeError('Something strange has occured')

    eci_client = NetStation(IP, port)
    eci_client.connect(ntp_ip = IP_amp)
    eci_client.begin_rec()
    sleep(2)
    eci_client.resync()
    eci_client.send_event(
        event_type = 'hipm',
        label = 'labl',
        desc = 'desc',
        data = {'text': 'mytext'}
    )
    eci_client.send_event(
        start = -1.0,
        event_type = 'hijt',
        data = {'monk': 'face'}
    )
    eci_client.end_rec()
    eci_client.disconnect()
if __name__ == '__main__':
    main()
