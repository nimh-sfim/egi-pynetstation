# PsychoPy3_NTP

## About this tool
This python package enables the use of Network Time Protocol (NTP) to synchronize EGI devices while using [PsychoPy3](https://www.psychopy.org/index.html).
The code is based on the (MATLAB-based) NTP code for [Psychtoolbox-3](http://psychtoolbox.org/docs/NetStation).



## How to use
To set up the python environment:
```
conda env create -f eci_env.yml
conda activate evi_env
```
See `example.py`, `example_simple.py`, and `psychopy_test.py` for examples of how to use the NetStation API.
