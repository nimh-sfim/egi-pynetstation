from eci.NetStation import NetStation
from eci.eci import package_event

def main():
    # Make a broadcasting socket
    eci_client = NetStation('127.0.0.1', 9885)
    eci_client.connect()
    eci_client.begin_rec()
    bs = package_event(1, .002, 'abcd', 'abcd', 'abcd', 
            {'dat1': 5.24, 'dat2': 'dog', 'dat3': 7}
    )
    eci_client.send_event(bs)
    eci_client.end_rec()
    eci_client.disconnect()
if __name__ == '__main__':
    main()
