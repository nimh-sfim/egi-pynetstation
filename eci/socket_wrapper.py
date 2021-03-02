#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import socket
import sys

class Socket():
    """
    Wrapper for the built-in socket() class, to simplify communication
    """
    buffersize = 4096
    mode = 'rwb'

    def __init__(self, address: str, port: int):
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


    def connect(self):
        """
        Connect to the TCP/IP socket
        """

        self._socket = socket.socket( socket.AF_INET, socket.SOCK_STREAM)
        self._socket.connect( self._address )

  
    def disconnect(self):
        """
        Disconnect from the socket
        """
        self._socket.close()
        self._socket = None

    def write(self, data):
        """
        Write to the amp (send data)
        """
        if not self._socket:
            raise RuntimeError('You have not connected')
        response = self._socket.send(data)
        if not response:
            raise RuntimeError('Connection Dropped')
        return response
    
    def read(self):
        """
        Read data from amp. BLOCKS ON READING
        """
        if not self._socket:
            raise RuntimeError('You have not connected')
        return self._socket.recv(Socket.buffersize)

class UDP():
    """
    Wrapper for UDP protocol used in NTP synchronization

    Attributes
    ----------
    _address: tuple
        The representation of IPv4 address and port number
    _socket: socket.socket
        The socket to communicate with
    """

    def __init__(self, ip: str, port: int) -> None:
        """
        Construct a UDP object; does not connect
        
        Parameters
        ----------
        ip: str
            The IPv4 address to connect to
        port: int
            The port number to connect with
        """
        self._address = (ip, port)
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._bound = False

    
    def connect(self):
        """
        Establish a UDP connection with amp
        """
        self._socket.bind(self._address)
        self._bound = True

    def disconnect(self):
        """
        End the UDP connection with amp
        """
        self._socket.close()

    def write(self, data):
        """
        Write a datagram to amp
        """
        if not self._bound:
            raise RuntimeError('UDP socket not bound')
        sent = self._socket.sendto(data, self._address)
        return sent
    
    def read(self):
        """
        Read response from amp.
        """
        rx_data, _ = self._socket.recvfrom(4096)
        return rx_data
