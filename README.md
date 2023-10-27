# EGI PyNetStation

## About this tool
This python package enables the use of a Python API to synchronize with a
NetStation amplifier.
You can install the latest "stable" version from pip (though even these are
still experimental):

```
pip install egi-pynetstation
```

If you would like to install the current codebase, you can clone it:

```
git clone https://github.com/nimh-sfim/egi-pynetstation.git
cd egi-pynetstation
pip install -e .
```

Quick example of how to use the package:

```
from egi_pynetstation import NetStation
# Set an IP address for the computer running NetStation as an IPv4 string
IP_ns = '10.10.10.42'
# Set a port that NetStation will be listening to as an integer
port_ns = 55513
ns = NetStation(IP_ns, port_ns)
# Set an NTP clock server (the amplifier) address as an IPv4 string
IP_amp = '10.10.10.51'
ns.connect(ntp_ip=IP_amp)
# Do whatever setup for your experiment here...
# Begin recording
ns.begin_rec()
# You can now send events; this one just says "HIYA" and automatically
# marks the time for you
ns.send_event(event_type="HIYA")
# You can include a data dictionary; perhaps you have a dog stimulus
my_data = {"dogs": "fido"}
# Send this data with the event type of "STIM"
ns.send_event(event_type="STIM", data=my_data)
# Occasionally resync the clocks, perhaps after each trial
ns.resync()
# With the experiment concluded, you can end the recording
ns.end_rec()
# You'll want to disconnect the amplifier when your program is done
ns.disconnect()
```
