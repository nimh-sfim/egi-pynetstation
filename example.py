"""Example of how to use NTP clock with NetStation"""

from eci.NetStation import NetStation
<<<<<<< HEAD
from time import sleep
=======
from eci.eci import package_event
from time import sleep, time
>>>>>>> josh_visit_nov_21


def high_res_sleep(amount: float):
    """Uses repeated time interrogations to try and sleep precisely"""
    t0 = time()
    while (time() - t0) < amount:
        continue

def main():
    IP = '10.10.10.42'
    IP_amp = '10.10.10.51'
    port = 55513

    t_minus = time()
    eci_client = NetStation(IP, port)
    eci_client.connect(ntp_ip=IP_amp)
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
