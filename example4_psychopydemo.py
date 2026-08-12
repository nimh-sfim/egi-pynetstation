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

  configure_auto_drift()  the package tracks when the next NTP drift
                      sample is due; your experiment decides when it is
                      safe to take one by calling sample_drift_if_due()
                      during an inter-trial interval.

Drift correction and drift sampling are both enabled by default, and
neither sends repeated ECI clock-sync commands.
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
    # Drift correction and the sampling schedule are both on by default,
    # so only the tuning is passed here. The package owns the schedule;
    # the experiment owns the timing safety window and calls
    # sample_drift_if_due() below. Nothing is sampled without that call
    # unless auto_drift_background=True.
    auto_drift_interval=15.0,
    auto_drift_min_pause=0.35,
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

        # Clear the screen and use the inter-trial interval. Tell the
        # package how much idle time it may use; it samples only when a
        # sample is due and there is room for it. This is an NTP query
        # only -- it does not send an ECI clock-sync command or create a
        # marker.
        win.flip()
        status = ns.sample_drift_if_due(available_pause=1.0)
        if status['sampled']:
            sample = status['sample']
            print(
                'NTP drift sample: offset={offset:.6f}s '
                'delay={delay:.6f}s valid={valid}'.format(**sample)
            )

        core.wait(1.0)

    print('Drift estimate:', ns.drift_estimate())

    # Asynchronous sends cannot raise into experiment code, so check here.
    errors = ns.event_errors()
    if errors:
        print(f'WARNING: {len(errors)} events failed to send:', errors[:3])
finally:
    if recording_started:
        ns.end_rec()    # flushes any events still queued
    ns.disconnect()
    win.close()
    core.quit()


# The contents of this file are in the public domain.
