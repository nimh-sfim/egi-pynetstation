#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import socket
import sys

class Socket():
    """
    Wrapper for the built-in socket() class, to simplify communication
    """

    def connect(self, str_address, port_no):
        """
        Connect to the amp at given ip and port
        """

        self._socket = socket.socket( socket.AF_INET, # IP_V4
                                      socket.SOCK_STREAM 
                                    )
        self._socket.connect(( str_address, port_no ))     

        self._connection = self._socket.makefile('rwb', 0) # read and write, no internal buffer     
  
    def disconnect(self):
        """
        Disconnect from amp
        """
        self._connection.close()
        self._socket.close()
        del self._connection
        del self._socket

    def write(self, data):
        """
        Write to the amp (send data)
        """
        self._connection.write(data)
    
    def read(self, size = -1):
        """
        Read data from amp. BLOCKS ON READING
        """
        if size < 0 :
            return self._connection.read()
        else :
            return self._connection.read( size )

class UDP():
    """
    Wrapper for UDP protocol used in NTP synchronization
    """
    
    def connect(self, str_addr):
        """
        Establish a UDP connection with amp
        """
        self._str_addr = str_addr
        self._socket = socket.socket( socket.AF_INET, socket.SOCK_DGRAM)

    def disconnect(self):
        """
        End the UDP connection with amp
        """
        self._socket.close()

    def write(self, data):
        """
        Write a datagram to amp
        """
        self._sent = self._socket.sendto(data, self._str_addr)
    
    def read(self, size=-1):
        """
        Read response from amp.
        """
        self._rx_data, self._server = self._socket.recvfrom(4096)