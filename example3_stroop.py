#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Minimal Stroop task with EGI Net Station event markers.

Five colour words shown in five ink colours, all 25 combinations in random
order. Five of those are congruent (word matches ink), twenty incongruent.

This is a reference for the smallest clean egi_pynetstation setup. Every
line that talks to the amplifier is marked with an "EGI:" comment. There
are only seven of them:

    1. NetStation(...)                 create the connection
    2. ns.connect(...)                 connect; events send off-thread
    3. ns.begin_rec()                  start recording (does the NTP sync)
    4. ns.wait_for_drift(...)          optional readiness countdown
    5. win.callOnFlip(ns.send_event)   mark stimulus onset on the flip
    6. ns.send_event(...)              mark the button press
    7. ns.end_rec() / ns.disconnect()  stop cleanly

Ongoing drift sampling needs no call at all -- it runs on the package's
own background thread, which is the default.

This task also demonstrates ns.wait_for_drift() as an optional warm-up
screen. It asks the package whether drift correction is ready yet and,
if not, displays the reason while the background sampler keeps working.
A run this short accumulates only a fraction of a millisecond of drift
either way, so the warm-up is not doing load-bearing work here -- it is
here to show the pattern for tasks where it would be.

Markers written to the recording:

    cong / inco   stimulus onset, congruent or incongruent
    resp          button press; description carries key and correctness
    miss          no response within the timeout

Press q or escape at any time to quit.
"""

import random

from psychopy import core, event, visual

from egi_pynetstation import NetStation


# --- Net Station addresses; change these for your setup ------------------
IP_NETSTATION = '10.10.10.42'   # computer running Net Station
ECI_PORT = 55513                # ECI TCP port configured in Net Station
IP_AMP = '10.10.10.51'          # amplifier / Net Station NTP server

# --- Task definition -----------------------------------------------------
# Response key for each ink colour.
COLORS = {
    'RED': 'r',
    'GREEN': 'g',
    'BLUE': 'b',
    'YELLOW': 'y',
    'PURPLE': 'p',
}
WORDS = list(COLORS)

INSTRUCTIONS = (
    'Name the INK COLOUR, not the word.\n\n'
    'r = red    g = green    b = blue\n'
    'y = yellow    p = purple\n'
)

# Optional: wait while the background sampler gets the drift model ready,
# so correction is active by trial 1 instead of engaging partway through
# the task. Do not read this as "drift is small": on validated hardware,
# a one-hour run with correction ON held a residual trend of +0.49
# ms/hour, but that is what correction leaves BEHIND, not what it
# removed. Left uncorrected, a real session can accumulate double-digit
# milliseconds of drift per hour, which is plenty to distort later ERP
# components -- do not disable drift correction (drift_correction=True is
# the default; leave it).
#
# What is negligible here is skipping this warm-up specifically for this
# short Stroop demo. The first answer from drift_ready() is the bool:
# ready or not ready. The reason string is the follow-up diagnostic for
# logs and operator messages: warming_up/settling can clear by waiting,
# while stalled/model_expired/sampling_expired mean the clock path needs
# attention. Set WARMUP_TIMEOUT_S = 0 to skip just the warm-up; the
# package still samples in the background and correction engages when it
# has enough evidence.
WARMUP_TIMEOUT_S = 300.0
WARMUP_POLL_S = 1.0

FIXATION_S = 0.500
RESPONSE_TIMEOUT_S = 2.000
ITI_S = 1.000
QUIT_KEYS = ['escape', 'q', 'Q']


def build_trials():
    """All 25 word/colour pairs, shuffled."""
    trials = [
        {'word': word, 'color': color, 'congruent': word == color}
        for word in WORDS
        for color in COLORS
    ]
    random.shuffle(trials)
    return trials


def wait_for_clock_ready(ns, win, message):
    """Show a clock-readiness countdown while background sampling runs.

    Optional. Nothing about this task requires it -- a Stroop run this
    short accumulates a negligible amount of drift whether or not
    correction ever engages. It is here so the pattern is on hand for a
    longer task, where starting with a ready clock model is worth waiting
    for.

    Returns False if the operator quit.

    wait_for_drift() is a readiness helper, not a timing primitive. It is
    safe here because no stimulus onset is being timed. Do not call it
    from a flip callback or inside a trial.
    """
    if WARMUP_TIMEOUT_S <= 0:
        return True

    def show_progress(status):
        if event.getKeys(keyList=QUIT_KEYS):
            raise KeyboardInterrupt
        eta = status['estimated_seconds_remaining']
        if status['ready']:
            body = 'Clock model ready.'
        elif eta is not None:
            body = f'Preparing the clock: about {eta:.0f} s left'
        else:
            body = f'Preparing the clock: {status["reason"]}'
        message.text = (
            INSTRUCTIONS
            + f'\n\n{body}\n\nPress q or escape to skip.'
        )
        message.draw()
        win.flip()

    try:
        # EGI 4: wait for drift readiness. The callback redraws the window
        # every poll; background sampling continues on the package thread.
        status = ns.wait_for_drift(
            timeout=WARMUP_TIMEOUT_S,
            poll=WARMUP_POLL_S,
            on_wait=show_progress,
        )
    except KeyboardInterrupt:
        return False

    if status['ready']:
        print('Drift correction ready before trial 1: '
              f"{status['slope_ms_per_hour']:.1f} ms/hour")
    else:
        print('Drift correction not ready before trial 1: '
              f"{status['reason']}; trials start with the current correction "
              'state.')
    return True


def main():
    # EGI 1: create the connection object.
    ns = NetStation(IP_NETSTATION, ECI_PORT)

    # EGI 2: connect. send_event() is non-blocking -- it captures the
    # timestamp and hands the socket write to a background thread -- which
    # is what makes it safe to call from a flip callback.
    #
    # Drift correction and background drift sampling are both on by
    # default; they are spelled out here so it is obvious what the
    # experiment is relying on. With auto_drift_background=True the
    # package samples NTP on its own thread, so the trial loop below never
    # has to ask for a sample.
    ns.connect(
        ntp_ip=IP_AMP,
        drift_correction=True,
        auto_drift=True,
        auto_drift_background=True,
        auto_drift_interval=15.0,
    )

    win = visual.Window(fullscr=True, color='black', units='height')
    word_stim = visual.TextStim(win, text='', height=0.15, bold=True)
    fixation = visual.TextStim(win, text='+', color='white', height=0.08)
    message = visual.TextStim(win, text='', color='white', height=0.05,
                              wrapWidth=1.2)

    trials = build_trials()
    results = []
    recording = False

    try:
        # EGI 3: begin recording. This also performs the one ECI NTP sync
        # that establishes the event timestamp epoch.
        ns.begin_rec()
        recording = True

        # Optional warm-up/readiness screen. This has to come after
        # begin_rec(), because begin_rec() establishes the NTP epoch that
        # drift_ready() and wait_for_drift() evaluate.
        if not wait_for_clock_ready(ns, win, message):
            return 0

        message.text = INSTRUCTIONS + '\n\nPress space to begin.'
        message.draw()
        win.flip()
        if event.waitKeys(keyList=['space'] + QUIT_KEYS)[0] in QUIT_KEYS:
            return 0

        readiness = ns.drift_ready()
        print('Clock readiness before trial 1:', readiness)

        for index, trial in enumerate(trials, start=1):
            # --- fixation ---
            fixation.draw()
            win.flip()
            core.wait(FIXATION_S)

            # --- stimulus ---
            word_stim.text = trial['word']
            word_stim.color = trial['color'].lower()
            word_stim.draw()

            # EGI 5: mark the onset on the flip that actually shows the
            # word. Event types must be exactly four ASCII characters, and
            # every data key must be exactly four characters too.
            win.callOnFlip(
                ns.send_event,
                event_type='cong' if trial['congruent'] else 'inco',
                label=f"{trial['word']}/{trial['color']}",
                data={
                    'trl_': index,
                    'word': trial['word'],
                    'colr': trial['color'],
                },
            )
            win.flip()

            # --- response ---
            clock = core.Clock()
            correct_key = COLORS[trial['color']]
            pressed, rt = None, None
            while clock.getTime() < RESPONSE_TIMEOUT_S:
                keys = event.getKeys(
                    keyList=list(COLORS.values()) + QUIT_KEYS,
                    timeStamped=clock,
                )
                if keys:
                    pressed, rt = keys[0]
                    break
                win.flip()

            if pressed in QUIT_KEYS:
                break

            is_correct = pressed == correct_key

            # EGI 6: mark the button press. Sent from the main loop rather
            # than a flip callback, which is fine -- it still captures the
            # timestamp immediately and returns.
            if pressed is not None:
                # The key and the outcome go in the description, so they
                # are readable in Net Station without decoding data keys.
                # desc accepts up to 256 ASCII characters.
                ns.send_event(
                    event_type='resp',
                    label=f'key {pressed}',
                    desc=(
                        f'key={pressed} '
                        f'{"correct" if is_correct else "incorrect"} '
                        f'target={correct_key}'
                    ),
                    data={
                        'trl_': index,
                        'key_': pressed,
                        'corr': is_correct,
                        'rt__': rt,
                    },
                )
            else:
                # No key within the timeout. Mark it anyway, or the trial
                # would have a stimulus with no matching response event.
                ns.send_event(
                    event_type='miss',
                    label='no response',
                    desc=f'no response within {RESPONSE_TIMEOUT_S:.3f} s',
                    data={'trl_': index},
                )

            results.append({
                'trial': index,
                'word': trial['word'],
                'color': trial['color'],
                'congruent': trial['congruent'],
                'response': pressed,
                'rt': rt,
                'correct': is_correct,
            })

            # --- inter-trial interval ---
            win.flip()

            # Nothing to do here for drift: the background sampler takes
            # care of it. An experiment that wanted explicit control over
            # exactly when NTP queries happen would pass
            # auto_drift_background=False and call
            # ns.sample_drift_if_due(available_pause=ITI_S) right here.
            core.wait(ITI_S)

        # --- summary ---
        answered = [r for r in results if r['response'] is not None]
        if answered:
            correct = [r for r in answered if r['correct']]
            print(f'{len(correct)}/{len(answered)} correct')
            for name, want in (('congruent', True), ('incongruent', False)):
                rts = [r['rt'] for r in correct if r['congruent'] is want]
                if rts:
                    print(f'  mean {name} RT: {sum(rts) / len(rts):.3f} s')

        # Asynchronous sends cannot raise into experiment code, so nothing
        # above would have told you if a marker never arrived. One call
        # covers that plus the drift model and any rejected ECI response.
        summary = ns.session_summary()
        if summary['ok']:
            print('Session OK:', summary)
        else:
            print('WARNING: check this session before analysing it:')
            print(f'  {summary}')
            for failure in ns.event_errors()[:3]:
                print(f'  failed send: {failure}')
    finally:
        # EGI 7: close cleanly. Both of these flush any queued events first,
        # so markers sent on the last trial still reach Net Station.
        if recording:
            ns.end_rec()
        ns.disconnect()
        win.close()

    return 0


if __name__ == '__main__':
    raise SystemExit(main())


# NOTE ON THE WARM-UP
# Drift correction does not report ready until the model has enough
# evidence and any first level correction has finished settling. By
# default that means 13 NTP samples spanning at least 180 seconds, so the
# 25 trials here may finish before readiness arrives unless the experiment
# connects early or waits before trial 1.
#
# wait_for_clock_ready() does not collect samples itself. It keeps the
# instruction screen alive while the package's background sampler does
# the same work it would already be doing. Set WARMUP_TIMEOUT_S = 0 to
# skip the wait; timestamps are still correct, just not necessarily
# drift-corrected yet.
#
# Do not try to shorten it by sampling faster. drift_min_span gates the
# fit at whatever value it holds, so with the 180 s default, samples
# crammed into less time produce no fit at all. Lowering the threshold is
# allowed but does not help: accuracy comes from the span actually
# sampled, not the threshold. At a 30 s span the slope error (~33 ms/hour)
# exceeds the drift being corrected (~17 ms/hour), so the correction would
# be worse than none -- which is what the default guards against. See
# docs/psychopy.rst.
