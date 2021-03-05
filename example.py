from eci.NetStation import NetStation

def main():
    # Make a broadcasting socket
    eci_client = NetStation('127.0.0.1', 9885)
    eci_client.connect()
    eci_client._command('BeginRecording')
    eci_client._command('EndRecording')
    print(eci_client._command('NTPReturnClock', 48))
    eci_client._command('Exit')
    eci_client.disconnect()
if __name__ == '__main__':
    main()
