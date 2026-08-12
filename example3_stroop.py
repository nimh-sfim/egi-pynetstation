#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Minimal Stroop task with EGI Net Station event markers.

Five colour words shown in five ink colours, all 25 combinations in random
order. Five of those are congruent (word matches ink), twenty incongruent.

This is a reference for the smallest clean egi_pynetstation setup. Every
line that talks to the amplifier is marked with an "EGI:" comment. There
are only eight of them:

    1. NetStation(...)                 create the connection
    2. ns.connect(...)                 connect; events send off-thread
    3. ns.begin_rec()                  start recording (does the NTP sync)
    4. ns.sample_drift()               warm the clock model up
    5. win.callOnFlip(ns.send_event)   mark stimulus onset on the flip
    6. ns.send_event(...)              mark the button press
    7. ns.sample_drift_if_due(...)     let the clock model stay current
    8. ns.end_rec() / ns.disconnect()  stop cleanly

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

# Warming the drift model up while the participant reads the instructions.
# The model needs WARMUP_SAMPLES samples spanning at least drift_min_span
# seconds (180 by default) before correction engages, so 13 x 15 s = 195 s
# clears it. Set WARMUP_SAMPLES = 0 to skip and start uncorrected.
WARMUP_SAMPLES = 13
WARMUP_INTERVAL_S = 15.0

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


def warm_up_drift_model(ns, win, message):
    """Collect drift samples while the instructions are on screen.

    Returns False if the operator quit.

    Shortening this window does not help. drift_min_span gates the fit at
    whatever value it holds, so with the 180 s default, samples crammed
    into less time produce no fit at all. Lowering the threshold lets a
    short window fit but does not improve it -- accuracy comes from the
    span actually sampled, which is why the gate exists. Span buys more
    than count: slope uncertainty falls as 1/span but only as 1/sqrt(n).
    """
    for index in range(WARMUP_SAMPLES):
        remaining = (WARMUP_SAMPLES - index) * WARMUP_INTERVAL_S
        message.text = (
            INSTRUCTIONS
            + f'\n\nPreparing the clock: about {remaining:.0f} s left'
        )
        message.draw()
        win.flip()

        # EGI 4: take a sample now. Safe here because we know nothing is
        # being timed -- never do this near a flip that matters.
        ns.sample_drift()

        timer = core.Clock()
        while timer.getTime() < WARMUP_INTERVAL_S:
            if event.getKeys(keyList=QUIT_KEYS):
                return False
            core.wait(0.05)
    return True


def main():
    # EGI 1: create the connection object.
    ns = NetStation(IP_NETSTATION, ECI_PORT)

    # EGI 2: connect. send_event() is non-blocking -- it captures the
    # timestamp and hands the socket write to a background thread -- which
    # is what makes it safe to call from a flip callback.
    # Drift correction and drift sampling are both on by default;
    # auto_drift_interval only tunes how often the clock model is
    # refreshed. The experiment still decides when it is safe, by calling
    # sample_drift_if_due() during the ITI. Without that call nothing is
    # sampled -- pass auto_drift_background=True if you would rather the
    # package sample on its own thread.
    ns.connect(
        ntp_ip=IP_AMP,
        auto_drift_interval=30.0,
        auto_drift_min_pause=0.35,
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

        # Warm the drift model up while the participant reads, so the
        # first trial is already drift-corrected. This has to come after
        # begin_rec(), because sample_drift() needs the NTP sync it does.
        if WARMUP_SAMPLES and not warm_up_drift_model(ns, win, message):
            return 0

        message.text = INSTRUCTIONS + '\n\nPress space to begin.'
        message.draw()
        win.flip()
        if event.waitKeys(keyList=['space'] + QUIT_KEYS)[0] in QUIT_KEYS:
            return 0

        state = ns.clock_state()
        if state['drift_accepted_fits']:
            print('Drift correction engaged before trial 1: '
                  f"{state['active_drift_slope'] * 3.6e6:.1f} ms/hour")
        else:
            print('Drift correction not yet engaged; trials start '
                  'uncorrected.')

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

            # EGI 7: offer the package the idle time it may use. It samples
            # only if one is due and the pause is long enough, so this never
            # lands near a flip.
            ns.sample_drift_if_due(available_pause=ITI_S)
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

        # Asynchronous sends cannot raise into experiment code, so check.
        errors = ns.event_errors()
        if errors:
            print(f'WARNING: {len(errors)} events failed to send:', errors[:3])
    finally:
        # EGI 8: close cleanly. Both of these flush any queued events first,
        # so markers sent on the last trial still reach Net Station.
        if recording:
            ns.end_rec()
        ns.disconnect()
        win.close()

    return 0


if __name__ == '__main__':
    raise SystemExit(main())


# NOTE ON THE WARM-UP
# Drift correction does not engage until the model has enough evidence:
# 13 NTP samples spanning at least 180 seconds by default. The 25 trials
# here take only about two minutes, so without a warm-up the correction
# would never activate at all. warm_up_drift_model() collects those
# samples while the instructions are on screen, which is time a real
# experiment is spending anyway.
#
# That does mean the instruction screen sits for about 195 seconds. Set
# WARMUP_SAMPLES = 0 to skip it; timestamps are still correct, just
# uncorrected for drift.
#
# Do not try to shorten it by sampling faster. drift_min_span gates the
# fit at whatever value it holds, so with the 180 s default, samples
# crammed into less time produce no fit at all. Lowering the threshold is
# allowed but does not help: accuracy comes from the span actually
# sampled, not the threshold. At a 30 s span the slope error (~33 ms/hour)
# exceeds the drift being corrected (~17 ms/hour), so the correction would
# be worse than none -- which is what the default guards against. See
# docs/psychopy.rst.
