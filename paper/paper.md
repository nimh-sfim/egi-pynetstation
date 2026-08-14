---
title: 'egi-pynetstation: Drift-corrected event marking for EGI EEG experiments in Python'
tags:
  - Python
  - electroencephalography
  - event-related potentials
  - experimental control
  - time synchronization
authors:
  - name: Joshua B. Teves
    affiliation: 1
  - name: Peter J. Molfese
    orcid: 0000-0002-3045-9408
    affiliation: 1
affiliations:
  - name: National Institute of Mental Health, National Institutes of Health, Bethesda, Maryland, United States
    index: 1
date: 13 August 2026
bibliography: paper.bib
---

# Summary

Electroencephalography (EEG) experiments need a dependable account of when a
stimulus, response, or other experimental event occurred. Those event markers
are used to segment continuous recordings into trials and to form
event-related potentials (ERPs), where millisecond-scale timing error can
blur the relationship between neural activity and the event under study
[@luck2014; @bridges2020]. EGI Net Station and Amp Server Pro systems accept such markers
through their Experimental Control Interface (ECI), a network protocol that
also provides commands for setting the event-timestamp epoch using the Network
Time Protocol (NTP) [@egi2015; @mills2010ntp].

At the start of a recording, an ECI `NTPClockSync` command transmits the
current NTP time to Net Station and establishes the reference epoch from which
subsequent ECI event-start values are interpreted. Each event timestamp is
then expressed as elapsed seconds since that synchronization point. The command
establishes the initial clock relationship, but does not account for later
divergence between the stimulus computer and acquisition clocks.

`egi-pynetstation` is an open-source Python package for controlling EGI
recordings and delivering timestamped ECI events. It is intended for
researchers developing behavioral, cognitive, and clinical EEG experiments in
Python, including experiments built with PsychoPy [@peirce2019psychopy2]. The
package provides a small high-level API for connecting to Net Station,
starting and stopping recordings, and sending events. Rather than treating
`NTPClockSync` as a recurring correction mechanism, it uses the initial
synchronization as a fixed epoch and continuously estimates the evolving
acquisition-to-stimulus-clock offset. Its timing support captures an event's
timestamp on the stimulus-control thread, queues the network operation for a
background sender, and applies the predicted drift correction to that captured
time. The result lets an experiment mark a PsychoPy screen flip without making
that flip wait for a network round trip.

# Statement of need

ERP analysis depends on aligning recorded EEG with the events that elicited
it. This requires more than sending an event label eventually: the label must
be associated with the intended moment in the experiment, often the display
refresh that made a visual stimulus visible. A blocking network operation in a
stimulus presentation loop can delay a subsequent frame, while a timestamp
taken after a network operation describes delivery latency instead of stimulus
onset. Over longer recordings, independent clocks on the stimulus computer and
the acquisition system can also drift apart. These failures are consequential
because they can introduce temporal error or variability into ERP epochs
[@luck2014].

The apparent remedies are inadequate. Holding the initial NTP offset constant
assumes the stimulus and acquisition clocks run at the same rate. Repeating ECI
clock-sync commands during a run can reset the event-time epoch. Applying raw
NTP offsets directly is also unsafe because NTP measures offset against the
operating system's adjustable wall clock, rather than the monotonic clock used
to timestamp experimental actions. `egi-pynetstation` resolves this tension by
maintaining one ECI epoch while estimating and compensating for the changing
clock relationship in the experiment's monotonic time frame.

EGI supplies acquisition software and documents a public ECI protocol, but it
does not supply a general-purpose, open stimulus-control environment. Instead,
laboratories commonly integrate their own task code or vendor-supported
packages such as E-Prime. The ECI documentation specifies NTP clock-sync
commands, but protocol documentation alone does not provide working Python
patterns for establishing an event-time epoch, avoiding repeated epoch resets,
or maintaining timing through a long experiment [@egi2015]. Researchers who
adopt open-source stimulus software therefore need a robust bridge between
their experimental-control code and EGI acquisition hardware.

The target audience is researchers and technical staff who run EGI EEG studies
from Python, especially those using PsychoPy. PsychoPy makes reproducible,
cross-platform experiment development accessible to a broad research community
[@peirce2019psychopy2], but its general-purpose stimulus API does not
implement ECI's clock semantics or drift management. `egi-pynetstation`
addresses this gap with an installable Python package, tested ECI message
handling, a documented PsychoPy flip-callback workflow, and diagnostics that
help users assess whether a stimulus computer's clocks are suitable for
drift-corrected operation.

# State of the field

The ECI specification is the authoritative description of the commands
understood by EGI systems, and it is necessarily the foundation for this
package [@egi2015]. It is not, however, an executable client library or an
experiment-timing design. Existing stimulus applications and generic network
clients can send data to hardware, but they leave experiment authors to decide
when to timestamp an event, whether a socket operation is safe on a rendering
thread, and how to account for changes in clock offset during a recording.

PsychoPy is the closest complementary open-source ecosystem: it provides
visual presentation, response collection, and a precise point at which
callbacks can be scheduled for a display flip [@peirce2019psychopy2]. Rather
than duplicate those capabilities, `egi-pynetstation` integrates with them.
Its `send_event` method can be passed directly to `Window.callOnFlip`, while
the package owns ECI serialization, timestamp conversion, asynchronous socket
delivery, and timing diagnostics. This division of responsibility permits
researchers to use established open experimental-control tools while gaining
hardware-specific timing support.

# Software design

The package uses one ECI `NTPClockSync` operation to establish the event
timestamp epoch at the start of a recording. Repeating that command during a
run would reset the epoch, so subsequent maintenance is performed by querying
the amplifier or Net Station NTP server. Each accepted NTP sample is expressed
relative to the local monotonic clock, rather than the adjustable system
clock. This prevents operating-system clock discipline from being mistaken for
acquisition-clock drift. A quality-gated, rolling linear model estimates the
changing NTP offset and corrects timestamps derived from the monotonic clock.
The model rejects high-delay samples and poor fits, limits extrapolation when
a fit becomes stale, and slews between accepted model levels so a newly
accepted estimate does not step event timestamps.

Timing-sensitive event delivery is separated from network I/O. `send_event`
records `time.monotonic()` immediately on the calling thread and places the
event on a queue. A background worker converts that captured value into a
drift-corrected ECI timestamp and writes it to the socket. The asynchronous
path preserves event order and captures the time of the experimental action,
not the eventual completion of the network write. A synchronous `wait=True`
mode remains available for diagnostics or commands whose response is needed.

Drift samples can be scheduled cooperatively during known idle intervals, or
automatically in a background thread for experiments where the author cannot
reliably arrange such intervals. The package also exposes model state, sample
history, and structured error records so timing behavior can be inspected
after a session. Unit tests cover ECI encoding and parsing, queue behavior,
timestamp capture, drift-model acceptance and rejection, response errors, and
package metadata. Continuous integration runs the test suite and builds the
documentation.

# Research impact statement

The package is publicly released on PyPI, maintained in an openly accessible
GitHub repository, and documented with installation, API, diagnostics, and
PsychoPy integration guides. Its near-term research significance is supported
by a one-hour continuous-recording validation against a photocell: the
marker-to-photocell offset had a standard deviation of 0.94 ms and a residual
trend of +0.49 ms/hour. During that recording, the operating system stepped
the system clock by 256 ms; expressing the offset in the monotonic-clock frame
kept the event-time model stable. The repository includes the validation
example and the clock-checking utility used to make this timing design
inspectable on new stimulus computers.

This combination of an open ECI implementation, an experiment-safe event
path, and explicit drift management gives laboratories a reusable alternative
to bespoke hardware-control code. It is particularly useful for EEG projects
that want the transparency and reproducibility of Python and PsychoPy without
asking each experiment author to reconstruct the acquisition system's timing
semantics. Lab Streaming Layer (LSL) addresses the same broad synchronization
problem through a different architecture, providing common time bases for
multiple streamed data sources on a local network [@kothe2025lab]. In contrast,
`egi-pynetstation` directly controls EGI acquisition and maintains its ECI
event-time epoch. Its photocell validation demonstrates practical event-timing
precision at the 1 to 3 ms scale, making either approach suitable for many
millisecond-sensitive neurobehavioral workflows while serving different
experimental architectures. Recent vendor-supported LSL connectivity for EGI
AmpServer hardware makes a direct, same-hardware comparison of ECI-derived
event timestamps and LSL stream timestamps feasible. Such a benchmark would
distinguish agreement in practical event timing from differences in
synchronization architecture and is a natural next validation for
`egi-pynetstation`.

# AI usage disclosure

Generative AI was used to assist with drafting portions of the software
documentation and to aid in formatting this manuscript. The authors reviewed 
and edited the resulting text, verified all technical claims against the 
implementation and validation materials, and take responsibility for the software 
and paper.

# Acknowledgements

The authors thank the National Institutes of Health (NIH) for supporting
the development context for this software. Add any applicable grant or
intramural funding acknowledgements before submission.

The contributions of the NIH author(s) were made as part of their
official duties as NIH federal employees, are in compliance with 
agency policy requirements, and are considered Works of the United 
States Government. However, the findings and conclusions presented in this paper
are those of the author(s) and do not necessarily reflect
the views of the NIH or the U.S. Department of Health
and Human Services. 

PAB Annual Report: ZIAMH-002783


# References
