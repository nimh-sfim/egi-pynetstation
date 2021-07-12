"""Example of how to use NTP clock with NetStation"""

from eci.NetStation import NetStation
from time import sleep


def main():
    IP = '10.10.10.42'
    IP_amp = '10.10.10.51'
    port = 55513

    eci_client = NetStation(IP, port)
    eci_client.connect(ntp_ip=IP_amp)
    eci_client.begin_rec()
    sleep(1)
    eci_client.send_event(event_type='first')
    name = 't %2.2d' % 0
    for i in range(10):
        sleep(.1)
        name = 't %2.2d' % i
        eci_client.send_event(event_type=name)
    eci_client.end_rec()
    eci_client.disconnect()


if __name__ == '__main__':
    main()
