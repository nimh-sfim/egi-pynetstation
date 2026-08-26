#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import socket
from .exceptions import *


class Socket():
    """
    Wrapper for the built-in socket() class, to simplify communication
    """
    buffersize = 4096
    timeout = 1
    # Applied to the connect() call itself. Without a separate value the
    # OS default (over a minute on some platforms) governs how long an
    # unreachable amplifier blocks before reporting failure.
    connect_timeout = 5

    def __init__(self, address: str, port: int) -> None:
        """
        Construct Socket object; does not connect.

        Parameters
        ----------
        address: str
            The IPv4 address the socket will use
        port: int
            The port number the socket will use
        """
        self._address = (address, port)
        self._socket = None

    def connect(self) -> None:
        """
        Connect to the TCP/IP socket

        Raises
        ------
        ConnectionRefusedError if the address is unavailable
        """
        # create_connection() applies the timeout to the connect itself.
        # Setting it afterwards, as this used to, left an unreachable host
        # blocked on the operating system's own much longer default.
        self._socket = socket.create_connection(
            self._address, timeout=Socket.connect_timeout,
        )
        self._socket.settimeout(Socket.timeout)

    def disconnect(self) -> None:
        """
        Disconnect from the socket
        """
        if self._socket:
            self._socket.close()
            self._socket = None

    def write(self, data: bytes) -> None:
        """
        Write to the socket

        Parameters
        ----------
        data: bytes
            The data to write to the socket

        Raises
        ------
        SocketIncompleteTransmission if the full data is not transmitted
        """
        length_data = len(data)
        if not self._socket:
            self.connect()
        try:
            # sendall(), not send(): a short write is legal on a stream
            # socket, and send() used to leave the prefix of a command in
            # the stream and carry on. Every later command would then be
            # parsed by the server as a continuation of a truncated one.
            self._socket.sendall(data)
        except socket.timeout as err:
            # A timeout mid-send means an unknown number of bytes landed,
            # so the command framing is no longer known. Nothing sent
            # afterwards can be trusted; close rather than continue.
            self.disconnect()
            raise SocketIncompleteTransmission(0, length_data) from err
        except OSError:
            self.disconnect()
            raise

    def read(self) -> bytes:
        """
        Read data from amp. BLOCKS ON READING

        Returns
        -------
        The byte array from the socket
        """
        if not self._socket:
            # This used to be self._socket.connect(), which is an
            # AttributeError on None -- the branch could never do what it
            # was written to do.
            raise SocketException(
                'Attempted to read from a socket that is not connected'
            )
        return self._socket.recv(Socket.buffersize)
