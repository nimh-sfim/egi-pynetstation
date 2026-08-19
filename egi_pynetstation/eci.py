#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""ECI controls and returns; mostly for internal use"""

from struct import pack, unpack
from typing import Union

from .exceptions import *
from .util import sys_from_bytes, get_ntp_byte, get_ntp_float, sys_to_bytes

# Color codes for printing debug information
blue = '\u001b[34;1m'
reset = '\u001b[0m'

# Variable to map API documentation to the byte representation
byte_table = {
    "Query": b"Q",
    "NewQuery": b"Y",
    "Exit": b"X",
    "BeginRecording": b"B",
    "EndRecording": b"E",
    "Attention": b"A",
    "ClockSync": b"T",
    "NTPClockSync": b"N",
    "NTPReturnClock": b"S",
    "EventData": b"D",
}

requires_data = ("Query", "ClockSync", "NTPClockSync", "EventData",
                 'NTPReturnClock')
# NOTE: NTPReturnClock does not indicate a need to send an NTPv4 in the
# SDK documentation; however, testing indicates that it is required

allowed_endians = ("NTEL", "MAC-", "UNIX")

# Python converts the bytes to ints when indexing; this is more legible
# This is admittedly hacky but it works.
INT_VAL_I = 73
INT_VAL_S = 83

# compactly named for convenience; milliseconds per second
MPS = 1000


def build_command(cmd: str, data: object = None) -> bytes:
    """
    Builds a byte array for ECI from the provided string and data

    Parameters
    ----------
    cmd: the command to send
    data: the data associated with the command; may be one of several types

    Returns
    -------
    The array of bytes that should be sent over the network

    Raises
    ------
    InvalidECICommand if the command is invalid

    See also
    --------
    InvalidECICommand and subclasses in eci.exceptions.py
    """
    if cmd not in byte_table:
        raise InvalidECICmd(cmd)
    # the byte array to send
    tx = byte_table[cmd]
    # Inverted if to normal use to reduce indentation for most of function
    if cmd not in requires_data:
        if data is not None:
            # We were given data but can't use it
            raise ECINoDataAllowed(cmd, data)
        else:
            # We're done, simple command
            return tx
    if data is None:
        # We need data and don't have it
        raise ECIDataRequired(cmd)
    # From here forward, we assume that we have data to work with
    # There is a series of conditionals to determine whether the supplied
    # data is valid.
    if cmd == "Query":
        if data in allowed_endians:
            tx += data.encode("ASCII")
        else:
            raise ECIIllegalEndian(data)
    elif cmd == "ClockSync":
        if not isinstance(data, int):
            raise ECIClockNonInteger(data)
        else:
            tx += sys_to_bytes(data, 4)
    elif cmd == "NTPClockSync" or cmd == 'NTPReturnClock':
        try:
            tx += get_ntp_byte(data)
        except NTPException:
            raise ECINTPInvalid()
    # TODO: add package_event to the command builder so that it is all
    # validated automatically
    elif cmd == "EventData":
        if isinstance(data, bytes):
            tx += data
        else:
            raise ECIDataNotBytes(data)
    else:
        raise ECIUnknownException()
    return tx


def parse_response(bytearr: bytes) -> Union[bool, float, int]:
    """Parses ECI response

    Parameters
    ----------
    bytearr: the byte array to parse

    Returns
    -------
    Either True or the value of the ECI Identity

    Raises
    ------
    ECIResponseFailure for all failures
    ECIFailure if the amp responds with failure
    ECINoRecordingDeviceFailure if the failure is a result of no recording
    TypeError if the object passed isn't type bytes

    Notes
    -----
    The documentation on how the server should respond is somewhat sketchy.
    These validations were determined mostly through trial and error.
    To view deviations from documentation, please view the source code.
    """
    arrlength = 0
    # TODO: turn into a debug option
    # print(f'{blue}Received amp response: {bytearr}{reset}')
    if isinstance(bytearr, bytes):
        arrlength = len(bytearr)
        if arrlength == 1:
            if bytearr == b'Z' or bytearr == b'I':
                return True
            if bytearr == b'F':
                raise ECIFailure()
            if bytearr == b'R':
                raise ECINoRecordingDeviceFailure()
            if bytearr == b'\x01':
                # TODO: turn into a debug option
                # print('NetStation says 1')
                return True
            if bytearr == b'S':
                # TODO: turn into a debug option
                # print('NetStation says S')
                return True
            else:
                raise InvalidECIResponse(bytearr)
        elif arrlength == 3 and bytearr[0:1] == b'F':
            # Documented failure form: 'F' plus two status bytes. These
            # used to be left in the stream and misread as the next
            # command's response.
            raise ECIFailure(bytearr[1:])
        elif arrlength == 2:
            # Identify version number
            # NOTE: this deviates from the SDK documentation, which
            # indicates a 1-byte response
            if bytearr[0] == INT_VAL_I:
                return sys_from_bytes(bytearr[1:])
            else:
                raise InvalidECIResponse(bytearr)
        elif arrlength == 8:
            # We've been given an NTPv4-formatted bytearr
            return get_ntp_float(bytearr)
        elif arrlength == 9:
            # We got back an NTP timestamp or failed

            # NOTE: this return of size 9 bytes rather than 8 is not
            # properly documented in the SDK guide. Sometimes the amp
            # responds with "S" and other times "Z". The reason for this
            # behavior is unclear.
            (seconds, subseconds, char) = unpack('IIc', bytearr)
            if char == b'Z':
                # Amp
                # TODO: turn into a debug option
                # print(
                #     f'Above response is: NTP of {seconds} seconds and '
                #     f'{subseconds} subseconds'
                # )
                return seconds + subseconds * 2**-32
            else:
                # Try S start (amp or app)
                char = bytearr[0]
                # Note: we can't unpack cII because integer alignment
                # forces the char to occupy four bytes, rather than just
                # one. Since unpack is designed to unpack C-structures,
                # this alignment ends up being accounted for.
                (seconds, subseconds) = unpack('II', bytearr[1:])
                if char == INT_VAL_S:
                    return seconds + subseconds * 2**-32
                else:
                    raise InvalidECIResponse(bytearr)
        else:
            raise InvalidECIResponse(bytearr)
    else:
        raise InvalidECIResponse(bytearr)


# What shape of response each command produces. ECI has no length
# prefix, so the only reliable way to know where one response ends is to
# know which command is outstanding.
RESPONSE_STATUS = 'status'          # Z / R / \x01 / S, or F(+2 status)
RESPONSE_IDENTIFY = 'identify'      # I, or I + version byte
RESPONSE_NTP = 'ntp'                # 8- or 9-byte timestamp

RESPONSE_SHAPES = {
    'Query': RESPONSE_IDENTIFY,
    'NTPReturnClock': RESPONSE_NTP,
}

# Single-byte responses that stand alone.
_STATUS_SINGLETONS = (b'Z', b'R', b'\x01', b'S')


def response_shape(cmd: str) -> str:
    """The response shape expected for a command."""
    return RESPONSE_SHAPES.get(cmd, RESPONSE_STATUS)


def frame_response(bytearr: bytes, expect: str, final: bool = False):
    """Carve exactly one complete ECI response off the front of a buffer.

    Returns ``(token, consumed)``, or ``(None, 0)`` when more bytes are
    needed. TCP gives no guarantee that one server ``send()`` arrives as
    one client ``recv()``, so a reader has to be able to say "not yet"
    and come back with more.

    ``final`` means no further bytes are coming (the read timed out).
    Two response forms are genuinely ambiguous until then -- ``F`` alone
    versus ``F`` plus two status bytes, and the 8- versus 9-byte
    timestamp -- so those resolve to the shorter form only once nothing
    else arrives.

    Parameters
    ----------
    bytearr: the bytes received so far
    expect: one of RESPONSE_STATUS, RESPONSE_IDENTIFY, RESPONSE_NTP
    final: treat the buffer as complete rather than waiting for more
    """
    if not isinstance(bytearr, bytes):
        raise InvalidECIResponse(bytearr)
    if not bytearr:
        return (None, 0)

    first = bytearr[0:1]

    # Failure can answer any command, and the documented form carries two
    # status bytes. Leaving those in the stream is what used to poison the
    # *next* command's response.
    if first == b'F':
        if len(bytearr) >= 3:
            return (bytearr[:3], 3)
        return (bytearr[:1], 1) if final else (None, 0)

    if expect == RESPONSE_NTP:
        if len(bytearr) >= 9:
            # A leading S, or a trailing Z, marks the 9-byte form.
            if first == b'S' or bytearr[8:9] == b'Z':
                return (bytearr[:9], 9)
            return (bytearr[:8], 8)
        if len(bytearr) >= 8:
            # Could still be a 9-byte form whose last byte is in flight.
            return (bytearr[:8], 8) if final else (None, 0)
        return (None, 0)

    if expect == RESPONSE_IDENTIFY:
        if first == b'I':
            if len(bytearr) >= 2:
                return (bytearr[:2], 2)
            return (bytearr[:1], 1) if final else (None, 0)
        return (bytearr[:1], 1)

    if first in _STATUS_SINGLETONS:
        return (bytearr[:1], 1)
    # Unrecognised: hand one byte to parse_response so it raises with the
    # real bytes rather than this function inventing a diagnosis.
    return (bytearr[:1], 1)


def split_response_tokens(bytearr: bytes) -> list:
    """Split a socket read into ECI response-sized byte tokens.

    TCP reads can contain more than one ECI response. Net Station also
    appears to bundle delayed NTPReturnClock timestamps with leading or
    trailing status bytes, such as ``S + timestamp + Z``. This helper keeps
    ``parse_response`` strict while letting callers consume the stream one
    logical response at a time.
    """
    if not isinstance(bytearr, bytes):
        raise InvalidECIResponse(bytearr)

    tokens = []
    index = 0
    length = len(bytearr)
    singletons = (b'Z', b'F', b'R', b'\x01')

    while index < length:
        remaining = bytearr[index:]
        first = remaining[0:1]

        if first == b'S' and len(remaining) >= 9:
            tokens.append(remaining[:9])
            index += 9
        elif len(remaining) >= 9 and remaining[8:9] == b'Z':
            tokens.append(remaining[:9])
            index += 9
        elif len(remaining) >= 9 and remaining[8:9] == b'S':
            tokens.append(remaining[:8])
            index += 8
        elif len(remaining) == 8:
            tokens.append(remaining)
            index += 8
        elif first in singletons:
            tokens.append(first)
            index += 1
        elif first == b'I':
            # Query commonly returns bare I. Keep the historical parser's
            # two-byte identify response available when the read is exactly
            # that shape.
            if len(remaining) == 2:
                tokens.append(remaining)
                index += 2
            else:
                tokens.append(first)
                index += 1
        elif len(remaining) >= 8:
            tokens.append(remaining[:8])
            index += 8
        else:
            tokens.append(remaining)
            index = length

    return tokens


# Every field in an ECI event packet is fixed-width. Out-of-range input
# used to reach struct.pack() and die with an opaque struct.error naming
# neither the field nor the value, so these bounds are checked up front.
MAX_LABEL_CHARS = 255           # pack('B', ...)
MAX_DESC_CHARS = 255            # pack('B', ...)
MAX_DATA_KEYS = 255             # pack('B', ...)
MAX_KEY_DATA_BYTES = 65535      # pack('H', ...)
MAX_EVENT_BYTES = 65535         # pack('H', ...)
MIN_START_MILLIS = -2 ** 31     # pack('i', ...)
MAX_START_MILLIS = 2 ** 31 - 1
MAX_DURATION_MILLIS = 2 ** 32 - 1   # pack('I', ...)
MIN_LONG_VALUE = -2 ** 31       # pack('i', ...)
MAX_LONG_VALUE = 2 ** 31 - 1


def _ascii_bytes(value: str, field: str) -> bytes:
    """Encode a field as ASCII, naming it if that is not possible.

    ECI is an ASCII protocol. Without this the failure was a bare
    UnicodeEncodeError raised from inside packing, which said nothing
    about which field carried the offending character.
    """
    try:
        return value.encode('ascii')
    except UnicodeEncodeError as err:
        offender = value[err.start:err.end]
        raise TypeError(
            f'Event {field} must be ASCII; {offender!r} at position '
            f'{err.start} is not'
        ) from err


def package_event(
    start: float,
    duration: float,
    event_type: str,
    label: str,
    desc: str,
    data: dict,
):
    """Takes event information and creates appropriate byte string

    Parameters
    ----------
    start: the start time of the event in SECONDS from time of last NTP
    sync
    duration: the duration of the event in SECONDS
    event_type: a four-character string indicating the event type
    label: a <=255-character string for labeling the event
    desc: a <=255-character string for describing the event
    data: a dictionary where each value is a string, number, or boolean,
        and each key is a string. Use this to pass data.

    Notes
    -----
    Every field is fixed-width, so all of these are bounded: at most
    ``MAX_DATA_KEYS`` keys, ``MAX_KEY_DATA_BYTES`` per text value, and
    ``MAX_EVENT_BYTES`` for the whole packet; ``start`` and integer
    values are signed 32-bit, ``duration`` unsigned 32-bit. Everything
    must be ASCII-encodable. Violations raise ``TypeError`` naming the
    field and the limit, which is the convention this function already
    uses for its range checks.
    """
    # First, perform type-checking and top-level validation
    type_start = type(start)
    type_duration = type(duration)
    type_etype = type(event_type)
    type_label = type(label)
    type_desc = type(desc)
    type_data = type(data)

    if not (isinstance(start, float) or isinstance(start, int)):
        raise TypeError(
            f'Event start should be number or str, is {type_start}'
        )
    if not start >= 0:
        raise TypeError(f'Event start should be >= 0, is {start}')
    if not (isinstance(duration, float) or isinstance(duration, int)):
        raise TypeError(
            f'Event duration should be number, is {type_duration}'
        )
    if not (duration >= 0.001):
        raise TypeError(
            f'Event duration should be at least 0.001, is {duration}'
        )
    if not isinstance(event_type, str):
        raise TypeError(f'Event type should be str, is {type_etype}')
    len_etype = len(event_type)
    if not len(event_type) == 4:
        raise TypeError(
            f'Event type should have 4 characters, has {len_etype}'
        )
    if not isinstance(label, str):
        raise TypeError(f'Event label should be str, is {type_label}')
    len_label = len(label)
    # Bound is 255, not 256: the length is packed with pack('B', ...),
    # an unsigned char. A 256-character label used to clear this check
    # and then die with an opaque struct.error during packing.
    if not len_label <= MAX_LABEL_CHARS:
        raise TypeError(
            f'Event label should be <= {MAX_LABEL_CHARS} characters, '
            f'is {len_label}'
        )
    if not isinstance(desc, str):
        raise TypeError(
            f'Event description should be str, is {type_desc}'
        )
    len_desc = len(desc)
    if not len_desc <= MAX_DESC_CHARS:
        raise TypeError(
            f'Event description should be <= {MAX_DESC_CHARS} characters, '
            f'is {len_desc}'
        )
    if not isinstance(data, dict):
        raise TypeError(f'Event data should be dict, is {type_data}')

    # Begin creating the data block
    nkeys = len(data.keys())
    if not nkeys <= MAX_DATA_KEYS:
        raise TypeError(
            f'Event data should have <= {MAX_DATA_KEYS} keys, has {nkeys}'
        )

    # Build block for datagram header
    start_millis = int(start * MPS)
    duration_millis = int(duration * MPS)
    if not MIN_START_MILLIS <= start_millis <= MAX_START_MILLIS:
        raise TypeError(
            f'Event start of {start} s is {start_millis} ms, outside the '
            f'signed 32-bit field ECI uses '
            f'({MIN_START_MILLIS} to {MAX_START_MILLIS} ms)'
        )
    if not 0 <= duration_millis <= MAX_DURATION_MILLIS:
        raise TypeError(
            f'Event duration of {duration} s is {duration_millis} ms, '
            f'outside the unsigned 32-bit field ECI uses '
            f'(0 to {MAX_DURATION_MILLIS} ms)'
        )
    # TODO: turn into a debug option
    # print(
    #     f'Using start time of {start_millis} milliseconds'
    #     f' and duration of {duration_millis} milliseconds'
    # )
    block = (
        pack('i', start_millis) +
        pack('I', duration_millis) +
        _ascii_bytes(event_type, 'type') +
        pack('B', len_label) + _ascii_bytes(label, 'label') +
        pack('B', len_desc) + _ascii_bytes(desc, 'description') +
        pack('B', nkeys)
    )

    # Build blocks for key-value pairs
    key_block = b''
    for key, value in data.items():
        # Check this key's validity
        if not isinstance(key, str):
            type_key = type(key)
            raise TypeError(
                f'Event data keys should be str, but {key} is {type_key}'
            )
        elif len(key) != 4:
            len_key = len(key)
            raise TypeError(
                'Event data keys should have 4 characters;'
                f' {key} has {len_key}'
            )

        # Check the value's validity
        if isinstance(value, bool):
            ktype = 'bool'
            klen = 1
            kdata = pack('?', value)
        elif isinstance(value, float):
            ktype = 'doub'
            klen = 8
            kdata = pack('d', value)
        elif isinstance(value, int):
            if not MIN_LONG_VALUE <= value <= MAX_LONG_VALUE:
                raise TypeError(
                    f'Event data key {key} has integer value {value}, '
                    f'outside the signed 32-bit field ECI uses '
                    f'({MIN_LONG_VALUE} to {MAX_LONG_VALUE})'
                )
            ktype = 'long'
            klen = 4
            kdata = pack('i', value)
        elif isinstance(value, str):
            ktype = 'TEXT'
            kdata = _ascii_bytes(value, f'data key {key}')
            klen = len(kdata)
            if not klen <= MAX_KEY_DATA_BYTES:
                raise TypeError(
                    f'Event data key {key} has a {klen}-byte value; the '
                    f'ECI length field holds at most '
                    f'{MAX_KEY_DATA_BYTES} bytes'
                )
        else:
            type_value = type(value)
            raise TypeError(
                'Event data values should be str, bool, or numeric; is' +
                f'{type_value}'
            )

        # Build the key's block
        key_block += (
            _ascii_bytes(key, 'data key') +
            bytes(ktype, 'ascii') +
            pack('H', klen) +
            kdata
        )

    # Put all blocks together
    len_all_blocks = len(block) + len(key_block)
    if not len_all_blocks <= MAX_EVENT_BYTES:
        raise TypeError(
            f'Event packs to {len_all_blocks} bytes; the ECI length field '
            f'holds at most {MAX_EVENT_BYTES}. Shorten the label, '
            f'description, or data values'
        )

    datagram = pack('H', len_all_blocks) + block + key_block

    return datagram
