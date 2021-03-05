from eci.NetStation import NetStation

def main():
    # Make a broadcasting socket
    eci_client = NetStation('127.0.0.1', 9885)
    eci_client.connect()
    eci_client.begin_rec()
    eci_client.end_rec()
    eci_client.disconnect()
if __name__ == '__main__':
    main()
