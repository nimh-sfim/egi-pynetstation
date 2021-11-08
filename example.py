from eci.NetStation import NetStation
from eci.eci import package_event
from time import sleep, time

from argparse import ArgumentParser

def high_res_sleep(amount: float):
    """Uses repeated time interrogations to try and sleep precisely"""
    t0 = time()
    while (time() - t0) < amount:
        continue

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

    t_minus = time()
    eci_client = NetStation(IP, port)
    eci_client.connect(ntp_ip = IP_amp)
    eci_client.begin_rec()
    eci_client.send_event(event_type="STRT", start=0.0)
    t = time()
    print(f"t-minus was {t - t_minus}")
    namer = lambda x: 't %2.2d' % x

    for i in range(10):
        high_res_sleep(3)
        name = namer(i)
        t0 = time()
        eci_client.send_event(event_type=name)
        print(f"{time() - t0} seconds for a data packet")
        if (i % 4) == 0:
            eci_client.resync()

    eci_client.end_rec()
    eci_client.disconnect()
if __name__ == '__main__':
    main()
