"""
    Python interface to interact with EGI Netstation

    NTP Synch capability is included

"""

__version__ = '0.1.0'

from .exceptions import *
from .util import sys_to_bytes, get_ntp_byte
from .eci import build_command
