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
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.connect(self._address)
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
        length_transmitted = 0
        if not self._socket:
            self.connect()
        length_transmitted = self._socket.send(data)
        if length_transmitted != length_data:
            raise SocketIncompleteTransmission(
                length_transmitted, length_data
            )

    def read(self) -> bytes:
        """
        Read data from amp. BLOCKS ON READING

        Returns
        -------
        The byte array from the socket
        """
        if not self._socket:
            self._socket.connect()
        return self._socket.recv(Socket.buffersize)
