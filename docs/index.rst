.. egi-pynetstation documentation master file, created by
   sphinx-quickstart on Mon Nov 15 15:10:43 2021.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

Welcome to egi-pynetstation's documentation!
============================================

egi-pynetstation is designed to help users of the EGI MRI-compatible EEG
system to perform high-resolution event marking with a small Python API.


.. toctree::
   :maxdepth: 2
   :caption: Contents:


Installation
============

**Option 1**: To install this package, you can pull it from pyPI via

.. code-block:: bash

    pip install egi-pynetstation

**Option 2**: If you would like the latest version on GitHub, you can execute the
following from:

.. code-block:: bash

    git clone https://github.com/nimh-sfim/egi-pynetstation.git
    cd egi-pynetstation
    pip install .

It should be installed into the environment you're currently in.

**Option 3**: We are also happy to have partnered with `PsychoPy <https://psychopy.org>`_, 
which now includes egi-pynetstation in the standalone package without further
downloads or steps.


Examples
========

Currently, there is only one supported way to use the NetStation interface.
This involves the use of "Network Time Protocol" or NTP.
In the future we will support the "simple" clock option that EGI provides.
A full showcase can be found in our ``example.py`` file on GitHub, found
`here <https://github.com/nimh-sfim/egi-pynetstation/blob/main/example.py>`_.

You will always need to execute commands in the following order:

.. code-block:: python

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
    #occasionally good to resync with clock - maybe after each trial
    ns.resync()
    # With the experiment concluded, you can end the recording
    ns.end_rec()
    # You'll want to disconnect the amplifier when your program is done
    ns.disconnect()

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
