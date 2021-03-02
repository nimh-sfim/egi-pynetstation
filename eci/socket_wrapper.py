#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import socket
import sys

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
        """
        self._socket = socket.socket( socket.AF_INET, socket.SOCK_STREAM)
        self._socket.connect( self._address )
        self._socket.settimeout(Socket.timeout)

  
    def disconnect(self) -> None:
        """
        Disconnect from the socket
        """
        if self._socket:
            self._socket.close()
            self._socket = None

    def write(self, data: bytes) -> object:
        """
        Write to the amp (send data)

        data: 
        """
        if not self._socket:
            self.connect()
        response = self._socket.send(data)
        return response
    
    def read(self) -> bytes:
        """
        Read data from amp. BLOCKS ON READING
        """
        if not self._socket:
            self._socket.connect()
        return self._socket.recv(Socket.buffersize)
