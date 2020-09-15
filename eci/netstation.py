#!/usr/bin/env python
# -*- coding: utf-8 -*-

from .socket_wrapper import Socket, UDP
import struct
import time
import sys

# Python 3.7+ wrapper to the EGI Netstation GES hardware communication protocol
#
# This package provides a Python interface for connection with NetStation via a TCP/IP socket, 
# and allows for both regular and NTP synchronization to the amplifier
# 
# This code was written based on the "python-egi" package for PsychoPy (https://github.com/gaelen/python-egi)
# and the MATLAB NetStation package for the Psyctoolbox (https://github.com/Psychtoolbox-3/Psychtoolbox-3)
# 
# Ref. EGI Amp Server Pro SDK 2.1, Network protocols, data formats, technical details here: 
# https://mailman.ucsd.edu/pipermail/lsl-l/attachments/20160203/3fd6477c/attachment-0002.pdf

# The following is adapted from the "Amp Server Pro SDK 2.1" documentation.
# 
# --- Controller commands sent to amp ----
#   specific cmd        |   cmd |   data    |   description
# ----------------------|-------|-----------|----------------
#   eci_Query           |   Q   |   cccc    |   used to est. connection w/ amp, and specify endianness (NTEL, MAC+)
#   eci_NewQuery        |   Y   |   NONE    |   query for machine type and map to chip used by OS, 
#                                               return val is 1 byte (ECI version number)
#   eci_Exit            |   X   |   NONE    |   ends communication session w/ amp
#   eci_BeginRecording  |   B   |   NONE    |   tells server application to start recording
#                                               one byte returned
#   exi_EndRecording    |   E   |   NONE    |   tell server to end recording
#                                               one byte returned
#   eci_Attention       |   A   |   NONE    |   sent in advance of sync command
#   eci_ClockSynch      |   T   |   1111    |   non-NTP synchronization, send  signed 4 byte int (client's time, in ms)
#   eci_NTPClockSynch   |   N   |   1111    |   NTP synchronization, time sent represents client absolute start time in NTP time format
#                                               one byte returned by server
#   eci_NTPReturnClock  |   S   |   NONE    |   NTP synchronization, except server returns its start time in NTP format
#   synch                                       return is 8byte NTP val from server
#   eci_EventData       |   D   |   <data>  |   send data as timestamp event, 
#                                               timestamp (32 bit int) is relative from abs. start time
#                                               return val is 1 byte
# 
# --- ECI amp return values ----
#   specific cmd                    |  resp |   data    |   description
# ----------------------            |-------|-----------|----------------
#   eci_OK                          |   Z   |   1 byte  |   returns 1 byte, value 'Z'
#   eci_Failure                     |   F   |   1 byte  |   1 byte returned, value 'F'
#   eci_NoRecordingDevice Failure   |   R   |   1 byte  |   'R'
#   eci_Identify                    |   I   |   1 byte  |   'I'
# 
# --- Data Types ---
#   Data type   |   Description
#   ----------------------------
#   cccc        |   four character descriptor (usually printable ASCII characters)
#   b           |   a single signed byte (-128 to 127)
#   ss*         |   signed short int (2 bytes) (-32,768 to 32,767)
#   IIII*       |   signed long int (4 bytes) (-2,147,483,648 to 2,147,483,647)


# # # # # # # # # # #
#   global flags    #
# # # # # # # # # # #
# these are used to track stuff related to NTP
NSSTATUS=0
NSRECORDING=0
NTPSOC=0
GETNTPSYNCHED=0
NTPLOG=[]
_TS_LAST = 0

# # # # # # # # # # #
# support functions #
# # # # # # # # # # #

def get_endianness_string(_map={'little':'NTEL','big':'UNIX'}):
    """
    Check endianness of machine, returns 'NTEL' or 'UNIX'
    """
    key = sys.byteorder
    return _map[key]

def ms_localtime(warnme=True):
    """
    Returns local time in milliseconds. User can set warnme=False to suppress error messages
    """
    global _TS_LAST
    # Since we're using Python 3.7+, we an use time.time() to get time in SECONDS from epoch
    # then convert to ms after using localtime() to get today's date/time

    t_sec_epoch = time.time()
    t_loc = time.localtime(t_sec_epoch)
    ms = (t_loc[3]*60*60*1000)+(t_loc[4]*60*1000)+(t_loc[5]*1000)
    
    if warnme and (ms < _TS_LAST):
        # raise EgiError
        print('ERROR, ms < _TS_LAST, please resynchronize (call NetStation.Sync() again)')
    _TS_LAST = ms
    return ms

# # # # # # # # # # #
#      classes      #
# # # # # # # # # # #
class Error(Exception):
    """
    Base class for other exceptions
    """
    pass

class NSError(Error):
    """
    General exception class for the Netstation module
    """
    #  todo
    @staticmethod
    def check_type(string_key):
        pass

    @staticmethod
    def check_len(string_key):
        pass

    @staticmethod
    def try_as_int(i):
        pass

class _Format(object):
    """
    Wrapper around a dict used to properly format messages SENT TO to the amp. Used for all commands OTHER THAN NetStation.SendEvent().
    """
    def __init__(self):
        self._format_strings = {
            # commands:
            # Q, X, B, E, A, T, N, S, D
            # amp responses
            # Z, F, R, I
            # See struct documentation for formatting (https://docs.python.org/2/library/struct.html 7.3.2.1, 7.3.2.2)
            
            'Q': '=4s', # native 4 chars
            'X': '',    # no value
            'B': '',
            'E': '',
            'A': '',
            'T': '=l',  # signed long (4 bytes)
            'N': '=l',
            'S': '=d',  # nothing sent, BUT server returns 8 bytes (so we use this to decode)
            'D': None,  # variable length structure
            'Z': '=B',  # one character returned
            'F': '=h',  # a signed short int (2 byte) is returned
            'R': '=B',
            'I': '=B'   # one byte version code
        }

    def __getitem__(self, key):
        return self._format_strings.get(key)  # return key's corresponding value

    def format_length(self, key):
        """
        Return the number of the bytes to read or write for the given
        command code.
        """
        return struct.calcsize(self[key])
    
    def pack(self, key, *args):
        """
        Pack the args according to their specific format (see dict _format_strings)
        """
        # note about format, we append a '=c' to indicate native byte order and a cmd code
        # the lstrip is used to remove the '=' from value returned from self[key] dict above

        fmt = '=c' + self[key].lstrip('=')
        b_key = bytes(key,'utf-8')

        if key == 'T' or key == 'N':
            # In this case, a number is sent, so we don't need to encode *args in bytes
            result = struct.pack(fmt,b_key,*args)
            return result
        elif key == 'S':
            # special case, on decode we expect 8 bytes, but here we just want to send a char
            fmt = '=c' + ''
            result = struct.pack(fmt,b_key,*args)
            return result
        else:
            # Convert the key and *args to bytes (has to be done explicitly in Python 3.x in order to pack with struct)
            b_args = bytes(*args,'utf-8')
            result = struct.pack(fmt, b_key, b_args)
            return result

    def unpack(self, key, data):
        """
        Unpack data according to format indicated by key
        """
        return struct.unpack(self[key],data)

class _DataFormat(object):
    """
    """
    pass

class NetStation(object):
    """
    Netstation object to interact with the amplifier.
    For example:
        ns.connect('ip',port) will connect us to the amp.

    Note that in Python 3.x, we need to send the command characters (when NOT using pack() ) as bytes
    """

    def __init__(self):
        self._socket = Socket()
        self._udp = UDP()
        self._endianness = get_endianness_string()
        self._fmt = _Format()
        self._data_fmt = _DataFormat()
    
    def connect(self, str_address, port_no):
        """
        Connect to the Netstation machine via TCP/IP
        """
        self._socket.connect(str_address, port_no)

    def disconnect(self):
        """
        Close the TCP/IP connection.
        """
        self._socket.disconnect()
    
    def GetServerResponse(self, warnme=True):
        """
        Interpret the response from the amp, convert to True and False, report error messages if necessary
        
        User can set warnme=False when calling to suppress error messages, but this is not recommended.
        """
        # Character is returned as a byte from server, so we need to decode it.
        code_encoded = self._socket.read(1)
        code = code_encoded.decode("utf-8")

        if code == 'Z':
            # Success
            return True

        elif code == 'F':
            # Failure
            error_info_length = self._fmt.format_length(code)
            error_info = self._socket.read(error_info_length)
            info = repr(self._fmt.unpack(code, error_info))

            if warnme:
                err_msg = "server returned error: " + info
                raise NSError(err_msg)
            else:
                return False

        elif code == 'I':
            # version byte
            version_length = self._fmt.format_length(code)
            version_info = self._socket.read(version_length)
            version = self._fmt.unpack(code, version_info)

            self._egi_protocol_version = version[0]
            return self._egi_protocol_version

        elif code == 'S':
            # 8 byte NTP time returned from server
            time_len = self._fmt.format_length(code)
            print(time_len)
            time_data = self._socket.read(time_len) # read 8 bytes from data stream
            time_val = self._fmt.unpack(code, time_data)

            return time_val
        
        else:
            # Something else happened
            if warnme:
                raise NSError("Unexpected character code returned from device: {}",format(code))
            else:
                return False

    def BeginSession(self):
        """
        Start the communication session, providing the endianness of the machine with the controller cmd 'Q'
        """
        message = self._fmt.pack('Q', self._endianness)
        self._socket.write(message)
        return self.GetServerResponse()

    def EndSession(self):
        """
        End the communication session with cmd 'X'
        """
        self._socket.write(b'X')
        return self.GetServerResponse()

    def StartRecording(self):
        """
        Start recording to the selected (externally) file, cmd 'B'
        """
        self._socket.write(b'B')
        return self.GetServerResponse()

    def StopRecording(self):
        """
        Stop recording to the selected file, using cmd 'E'. The
        recording can be resumed with the BeginRecording() command 
        if the session is not closed yet.
        """
        self._socket.write(b'E')
        return self.GetServerResponse()
    
    def Attention(self):
        """
        Sends and 'Attention' command, cmd 'A'. Always done before synchronizing 
        (tells server not to change anything that may mess up synchronization)
        """
        self._socket.write(b'A')
        return self.GetServerResponse()

    def SendLocalTime(self, ms_time=None):
        """
        Send the local time (in ms) to Netstation; usually
        happens after an 'Attention' command.
        """
        if ms_time is None:
            ms_time = ms_localtime()
        message = self._fmt.pack('T', ms_time)
        self._socket.write(message)
        return self.GetServerResponse()

    def NTPReturnClock(self):
        """
        Request the amp server's NTP time (8 bytes returned)
        """
        self._socket.write(b'S')
        return self.GetServerResponse()

    def Synch(self, timestamp=None):
        """
        A shortcut for sending the 'attention' command and the localtime
        info.
        """
        if (self.Attention()) and (self.SendLocalTime(timestamp)):
            return True
        else:
            raise NSError("non-NTP sync command failed!")

    def Sync_NTP(self):
        """
        A shortcut for sendint the 'attention' command and the time info, syncing with NTP
        """
        pass
    
    def SendEvent(self, key, timestamp=None, label=None, description=None, table=None, pad=False):
        """
        Pack and send an event to the server. note that before sending an event, Sync() or Sync_NTP() must be called.

        ack = 0, don't wait for acknowledgement
        ack = 1, wait for acknowledgement
        """
