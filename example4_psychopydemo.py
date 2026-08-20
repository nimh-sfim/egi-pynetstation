#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Demonstrate sending EGI Net Station events from PsychoPy.

This example uses egi-pynetstation, which sends ECI commands and event
markers to Net Station / Amp Server Pro with NTP-based event timing.

Two behaviours do the work:

  send_event()        never blocks. It captures the timestamp on the
                      calling thread and returns in microseconds while a
                      background thread does the socket write, so it is
                      safe to call from win.callOnFlip() and equally safe
                      from ordinary experiment code. Pass wait=True only
                      if you need the ECI response back.

  background drift sampling  the package keeps its clock model current
                      on its own thread. Nothing in the trial loop has to
                      ask for a sample. This is the default, and it is
                      spelled out in connect() below so it is obvious
                      what the experiment relies on.

Drift correction and background drift sampling are both enabled by
default, and neither sends repeated ECI clock-sync commands.
"""

from psychopy import core, visual

from egi_pynetstation import NetStation


# Change these addresses for your EGI network.
IP_ns = '10.10.10.42'   # computer running Net Station
port_ns = 55513         # ECI TCP port configured in Net Station
IP_amp = '10.10.10.51'  # amplifier / Net Station NTP server


ns = NetStation(IP_ns, port_ns)
ns.connect(
    ntp_ip=IP_amp,
    # All three of these are already the defaults. They are written out
    # so the recommended configuration is visible at a glance: correct
    # for drift, sample automatically, and do the sampling on the
    # package's own thread so the trial loop never has to.
    drift_correction=True,
    auto_drift=True,
    auto_drift_background=True,
    auto_drift_interval=15.0,
)

win = visual.Window(fullscr=True, screen=0, color='black', units='height')
fixation = visual.TextStim(win, text='+', color='white', height=0.08)

recording_started = False
try:
    ns.begin_rec()
    recording_started = True

    ns.send_event(event_type='STRT', label='recording start', start=0.0)

    for trial in range(10):
        # Draw first, then request the marker on the flip that actually
        # makes the stimulus visible. send_event() blocks this thread for
        # only a few microseconds, so it is safe in the callback.
        fixation.draw()
        win.callOnFlip(
            ns.send_event,
            event_type='stim',
            label='stimulus',
            data={'trl_': trial, 'cond': 'demo'},
        )
        win.flip()
        core.wait(0.5)

        # Clear the screen for the inter-trial interval. Drift sampling
        # needs nothing here: the background thread handles it. An
        # experiment that wanted to control exactly when NTP queries
        # happen would pass auto_drift_background=False and call
        # ns.sample_drift_if_due(available_pause=1.0) at this point.
        win.flip()
        core.wait(1.0)

    # Asynchronous sends cannot raise into experiment code, so check here.
    # session_summary() is the one call worth making at the end of a run:
    # 'ok' is True only when drift correction engaged and is not stalled,
    # NTP sampling is current, and no event or ECI response failed.
    summary = ns.session_summary()
    print('Session summary:', summary)
    if not summary['ok']:
        print('WARNING: check this session before analysing it.')
finally:
    if recording_started:
        ns.end_rec()    # flushes any events still queued
    ns.disconnect()
    win.close()
    core.quit()


# The contents of this file are in the public domain.
