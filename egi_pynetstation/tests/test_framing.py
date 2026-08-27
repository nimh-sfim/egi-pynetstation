#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""ECI response framing over a fragmented TCP stream.

TCP is a byte stream: one server ``send()`` is not one client ``recv()``.
ECI responses carry no length prefix, so the only reliable way to know
where one ends is to know which command is outstanding.

Before this was command-aware, three things went wrong, each reproduced
here as a regression test:

* the documented ``F`` + two status bytes failure form left its status
  bytes in the stream, where they were read as the *next* command's
  response;
* a response split across two ``recv()`` calls was reported as invalid;
  and
* an 8-byte timestamp arriving in pieces was mis-split into a singleton,
  which ``parse_response`` decoded as a cheerful ``True`` -- a silently
  wrong answer rather than an error.
"""

import importlib
import time
import types

import pytest

from egi_pynetstation import egi_ntp

from egi_pynetstation.eci import (
    RESPONSE_IDENTIFY,
    RESPONSE_NTP,
    RESPONSE_STATUS,
    frame_response,
    response_shape,
)
from egi_pynetstation.exceptions import (
    ECIFailure,
    InvalidECIResponse,
    SocketException,
)

netstation_module = importlib.import_module('egi_pynetstation.NetStation')
NetStation = netstation_module.NetStation

# Data required by commands that take an argument.
COMMAND_DATA = {
    'Query': 'NTEL',
    'NTPReturnClock': 3900000000.5,
    'NTPClockSync': 3900000000.5,
}

TIMESTAMP = b'\x01' * 8          # decodes to 16843009.00392157
TIMESTAMP_VALUE = 16843009.00392157


class ChunkedSocket:
    """Serves a scripted byte stream, `chunks` bytes per read()."""

    def __init__(self, stream, chunks):
        self.buf = bytearray(stream)
        self.chunks = list(chunks)

    def connect(self):
        pass

    def disconnect(self):
        pass

    def write(self, data):
        pass

    def read(self):
        size = self.chunks.pop(0) if self.chunks else len(self.buf)
        size = min(size, len(self.buf))
        if size == 0:
            # Nothing further is coming, which is what a read timeout
            # looks like to the reader.
            raise TimeoutError('no more bytes scripted')
        out = bytes(self.buf[:size])
        del self.buf[:size]
        return out


def station(stream, chunks):
    ns = NetStation('127.0.0.1', 55513)
    ns._connected = True
    ns._socket = ChunkedSocket(stream, chunks)
    return ns


def send(ns, cmd):
    return ns._command(cmd, COMMAND_DATA.get(cmd), strict=True)


def every_split(stream):
    """Chunk plans that break `stream` at each interior byte boundary."""
    return [[i, len(stream) - i] for i in range(1, len(stream))]


# --- the shape table -----------------------------------------------------

def test_response_shape_maps_commands():
    assert response_shape('Query') == RESPONSE_IDENTIFY
    assert response_shape('NTPReturnClock') == RESPONSE_NTP
    for cmd in ('Attention', 'BeginRecording', 'EndRecording', 'EventData'):
        assert response_shape(cmd) == RESPONSE_STATUS


# --- frame_response, in isolation ---------------------------------------

def test_incomplete_buffers_ask_for_more():
    assert frame_response(b'', RESPONSE_STATUS) == (None, 0)
    assert frame_response(b'F', RESPONSE_STATUS) == (None, 0)
    assert frame_response(b'I', RESPONSE_IDENTIFY) == (None, 0)
    assert frame_response(TIMESTAMP[:5], RESPONSE_NTP) == (None, 0)
    assert frame_response(TIMESTAMP, RESPONSE_NTP) == (None, 0)


def test_final_resolves_the_ambiguous_forms():
    """`F` alone and the 8-byte timestamp are only decidable at the end."""
    assert frame_response(b'F', RESPONSE_STATUS, final=True) == (b'F', 1)
    assert frame_response(b'I', RESPONSE_IDENTIFY, final=True) == (b'I', 1)
    assert frame_response(TIMESTAMP, RESPONSE_NTP, final=True) == (TIMESTAMP, 8)


def test_status_singletons_consume_exactly_one_byte():
    for byte in (b'Z', b'R', b'\x01', b'S'):
        assert frame_response(byte + b'ZZZ', RESPONSE_STATUS) == (byte, 1)


def test_failure_form_consumes_its_status_bytes():
    """Leaving these behind is what poisoned the following command."""
    assert frame_response(b'F\x00\x02Z', RESPONSE_STATUS) == (b'F\x00\x02', 3)


def test_ntp_nine_byte_forms_are_recognised():
    assert frame_response(b'S' + TIMESTAMP, RESPONSE_NTP) == (b'S' + TIMESTAMP, 9)
    assert frame_response(TIMESTAMP + b'Z', RESPONSE_NTP) == (TIMESTAMP + b'Z', 9)


def test_frame_response_rejects_non_bytes():
    with pytest.raises(InvalidECIResponse):
        frame_response('not bytes', RESPONSE_STATUS)


# --- the validated path must not change ---------------------------------

@pytest.mark.parametrize('chunks', [[1, 1, 1], [3], [2, 1], [1, 2]])
def test_ordinary_z_responses_are_unaffected(chunks):
    """Every real recording is this path; it must be exactly as before."""
    ns = station(b'ZZZ', chunks)

    assert [send(ns, 'Attention') for _ in range(3)] == [True, True, True]


# --- regression: Fss no longer poisons the next command -----------------

@pytest.mark.parametrize('chunks', [[4]] + every_split(b'F\x00\x02Z'))
def test_failure_status_bytes_do_not_corrupt_the_next_command(chunks):
    ns = station(b'F\x00\x02Z', chunks)

    with pytest.raises(ECIFailure):
        send(ns, 'Attention')
    # The status bytes used to surface here as InvalidECIResponse.
    assert send(ns, 'Attention') is True


def test_failure_status_bytes_are_reported():
    ns = station(b'F\x00\x02', [3])

    with pytest.raises(ECIFailure) as err:
        send(ns, 'Attention')
    assert '0002' in str(err.value)


def test_bare_failure_byte_still_works():
    """Older/simpler replies must not start hanging or mis-decoding."""
    ns = station(b'F', [1])

    with pytest.raises(ECIFailure):
        send(ns, 'Attention')


# --- regression: fragmented responses reassemble ------------------------

@pytest.mark.parametrize('chunks', [[3]] + every_split(b'I\x02Z'))
def test_identify_response_survives_fragmentation(chunks):
    ns = station(b'I\x02Z', chunks)

    assert send(ns, 'Query') == 2
    assert send(ns, 'Attention') is True


@pytest.mark.parametrize(
    'chunks', [[10], [9, 1]] + every_split(b'S' + TIMESTAMP + b'Z') + [[1] * 10],
)
def test_ntp_timestamp_survives_fragmentation(chunks):
    """The S-prefixed 9-byte form, split at every boundary."""
    ns = station(b'S' + TIMESTAMP + b'Z', chunks)

    assert send(ns, 'NTPReturnClock') == pytest.approx(TIMESTAMP_VALUE)
    assert send(ns, 'Attention') is True


@pytest.mark.parametrize('chunks', [[8], [3, 5], [1] * 8])
def test_eight_byte_timestamp_is_not_mis_split(chunks):
    """This used to decode as True -- a silently wrong answer.

    A partial read was emitted as a b'\\x01' singleton, which
    parse_response maps to True, so a diagnostic asking for the server
    clock got a boolean instead of a timestamp and no error at all.
    """
    ns = station(TIMESTAMP, chunks)

    assert send(ns, 'NTPReturnClock') == pytest.approx(TIMESTAMP_VALUE)


def test_ntp_failure_reply_is_still_a_failure():
    ns = station(b'F\x00\x02Z', [4])

    with pytest.raises(ECIFailure):
        send(ns, 'NTPReturnClock')
    assert send(ns, 'Attention') is True


# --- connection close ---------------------------------------------------

def test_orderly_close_is_reported():
    """recv() == b'' means the peer is gone; reading again cannot help."""

    class ClosedSocket(ChunkedSocket):
        def read(self):
            return b''

    ns = NetStation('127.0.0.1', 55513)
    ns._connected = True
    ns._socket = ClosedSocket(b'', [])

    with pytest.raises(SocketException, match='closed'):
        send(ns, 'Attention')


def test_buffer_does_not_leak_between_connections(monkeypatch):
    """Half-read bytes must not be attributed to the next session."""
    monkeypatch.setattr(
        netstation_module, 'NTPClient',
        lambda: types.SimpleNamespace(
            request=lambda *a, **k: types.SimpleNamespace(
                offset=0.0, delay=0.002, tx_time=0.0,
                local_time=time.time(),
                monotonic_time=egi_ntp.monotonic_time(),
                python_monotonic_time=time.monotonic(),
            ),
        ),
    )

    class FakeSocket:
        def __init__(self, *a, **k):
            pass

        def connect(self):
            pass

        def write(self, data):
            pass

        def read(self):
            return b'Z'

        def disconnect(self):
            pass

    monkeypatch.setattr(netstation_module, 'Socket', FakeSocket)
    ns = NetStation('127.0.0.1', 55513)
    ns.connect(ntp_ip='10.10.10.51')
    ns._rx_buffer = b'leftover garbage'
    ns.disconnect()

    ns.connect(ntp_ip='10.10.10.51')
    try:
        assert ns._rx_buffer == b''
    finally:
        ns.disconnect()
