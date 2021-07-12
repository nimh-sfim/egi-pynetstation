#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
This experiment was created using PsychoPy3 Experiment Builder (v2021.1.2),
    on Thu Jul  8 13:29:59 2021
If you publish work using this script the most relevant publication is:

    Peirce J, Gray JR, Simpson S, MacAskill M, Höchenberger R, Sogo H, Kastman E, Lindeløv JK. (2019) 
        PsychoPy2: Experiments in behavior made easy Behav Res 51: 195. 
        https://doi.org/10.3758/s13428-018-01193-y

Modified by Joshua Teves to include NetStation API demonstration.
To find NetStation behavior, search for "ns".
"""

from __future__ import absolute_import, division

from psychopy import locale_setup
from psychopy import prefs
from psychopy import sound, gui, visual, core, data, event, logging, clock, colors
from psychopy.constants import (NOT_STARTED, STARTED, PLAYING, PAUSED,
                                STOPPED, FINISHED, PRESSED, RELEASED, FOREVER)

import numpy as np  # whole numpy lib is available, prepend 'np.'
from numpy import (sin, cos, tan, log, log10, pi, average,
                   sqrt, std, deg2rad, rad2deg, linspace, asarray)
from numpy.random import random, randint, normal, shuffle, choice as randchoice
import os  # handy system and path functions
import sys  # to get file system encoding

import time

from psychopy.hardware import keyboard

from eci.NetStation import NetStation

# Ensure that relative paths start from the same directory as this script
_thisDir = os.path.dirname(os.path.abspath(__file__))
os.chdir(_thisDir)

# Store info about the experiment session
psychopyVersion = '2021.1.2'
expName = 'psychopy_test'  # from the Builder filename that created this script
expInfo = {'participant': '', 'session': '001'}
dlg = gui.DlgFromDict(dictionary=expInfo, sortKeys=False, title=expName)
if dlg.OK == False:
    core.quit()  # user pressed cancel
expInfo['date'] = data.getDateStr()  # add a simple timestamp
expInfo['expName'] = expName
expInfo['psychopyVersion'] = psychopyVersion

# Data file name stem = absolute path + name; later add .psyexp, .csv, .log, etc
filename = _thisDir + os.sep + u'data/%s_%s_%s' % (expInfo['participant'], expName, expInfo['date'])

# An ExperimentHandler isn't essential but helps with data saving
thisExp = data.ExperimentHandler(name=expName, version='',
    extraInfo=expInfo, runtimeInfo=None,
    originPath='/Users/tevesjb/Downloads/psychopy_test.py',
    savePickle=True, saveWideText=True,
    dataFileName=filename)
# save a log file for detail verbose info
logFile = logging.LogFile(filename+'.log', level=logging.EXP)
logging.console.setLevel(logging.WARNING)  # this outputs to the screen, not a file

endExpNow = False  # flag for 'escape' or other condition => quit the exp
frameTolerance = 0.001  # how close to onset before 'same' frame

# Start Code - component code to be run after the window creation

# Setup the Window
win = visual.Window(
    size=(1024, 768), fullscr=True, screen=0, 
    winType='pyglet', allowGUI=False, allowStencil=False,
    monitor='testMonitor', color=[0,0,0], colorSpace='rgb',
    blendMode='avg', useFBO=True, 
    units='height')
# store frame rate of monitor if we can measure it
expInfo['frameRate'] = win.getActualFrameRate()
frameDur = 1/30.0

# create a default keyboard (e.g. to check for escape)
defaultKeyboard = keyboard.Keyboard()

# Initialize components for Routine "start_pause"
start_pauseClock = core.Clock()
WaitMessage = visual.TextStim(win=win, name='WaitMessage',
    text='Wait…',
    font='Open Sans',
    pos=(0, 0), height=0.1, wrapWidth=None, ori=0.0, 
    color='white', colorSpace='rgb', opacity=None, 
    languageStyle='LTR',
    depth=0.0);

# Initialize components for Routine "trial"
trialClock = core.Clock()
light = visual.Rect(
    win=win, name='light',
    width=(1.0, 1.0)[0], height=(1.0, 1.0)[1],
    ori=0.0, pos=(0, 0),
    lineWidth=1.0,     colorSpace='rgb',  lineColor='white', fillColor='white',
    opacity=None, depth=0.0, interpolate=True)
dark = visual.Rect(
    win=win, name='dark',
    width=[1.0, 1.0][0], height=[1.0, 1.0][1],
    ori=0.0, pos=(0, 0),
    lineWidth=1.0,     colorSpace='rgb',  lineColor='black', fillColor='black',
    opacity=None, depth=-1.0, interpolate=True)

# Initialize NetStation
ns_IP = '10.10.10.42'
ns_port = 55513
ns_ntp_IP = '10.10.10.51'
ns = NetStation(ns_IP, ns_port)
ns.connect(clock='simple')
ns.begin_rec()
ns_t = time.time()
# Create some handy timers
globalClock = core.Clock()  # to track the time since experiment started
routineTimer = core.CountdownTimer()  # to track time remaining of each (non-slip) routine 

# ------Prepare to start Routine "start_pause"-------
continueRoutine = True
routineTimer.add(1.000000)
# update component parameters for each repeat
# keep track of which components have finished
start_pauseComponents = [WaitMessage]
for thisComponent in start_pauseComponents:
    thisComponent.tStart = None
    thisComponent.tStop = None
    thisComponent.tStartRefresh = None
    thisComponent.tStopRefresh = None
    if hasattr(thisComponent, 'status'):
        thisComponent.status = NOT_STARTED
# reset timers
t = 0
_timeToFirstFrame = win.getFutureFlipTime(clock="now")
start_pauseClock.reset(-_timeToFirstFrame)  # t0 is time of first possible flip
frameN = -1

# -------Run Routine "start_pause"-------
while continueRoutine and routineTimer.getTime() > 0:
    # get current time
    t = start_pauseClock.getTime()
    tThisFlip = win.getFutureFlipTime(clock=start_pauseClock)
    tThisFlipGlobal = win.getFutureFlipTime(clock=None)
    frameN = frameN + 1  # number of completed frames (so 0 is the first frame)
    # update/draw components on each frame
    
    # *WaitMessage* updates
    if WaitMessage.status == NOT_STARTED and tThisFlip >= 0.0-frameTolerance:
        # keep track of start time/frame for later
        WaitMessage.frameNStart = frameN  # exact frame index
        WaitMessage.tStart = t  # local t and not account for scr refresh
        WaitMessage.tStartRefresh = tThisFlipGlobal  # on global time
        win.timeOnFlip(WaitMessage, 'tStartRefresh')  # time at next scr refresh
        WaitMessage.setAutoDraw(True)
    if WaitMessage.status == STARTED:
        # is it time to stop? (based on global clock, using actual start)
        if tThisFlipGlobal > WaitMessage.tStartRefresh + 1.0-frameTolerance:
            # keep track of stop time/frame for later
            WaitMessage.tStop = t  # not accounting for scr refresh
            WaitMessage.frameNStop = frameN  # exact frame index
            win.timeOnFlip(WaitMessage, 'tStopRefresh')  # time at next scr refresh
            WaitMessage.setAutoDraw(False)
    
    # check for quit (typically the Esc key)
    if endExpNow or defaultKeyboard.getKeys(keyList=["escape"]):
        core.quit()
    
    # check if all components have finished
    if not continueRoutine:  # a component has requested a forced-end of Routine
        break
    continueRoutine = False  # will revert to True if at least one component still running
    for thisComponent in start_pauseComponents:
        if hasattr(thisComponent, "status") and thisComponent.status != FINISHED:
            continueRoutine = True
            break  # at least one component has not yet finished
    
    # refresh the screen
    if continueRoutine:  # don't flip if this routine is over or we'll get a blank screen
        win.flip()

# -------Ending Routine "start_pause"-------
for thisComponent in start_pauseComponents:
    if hasattr(thisComponent, "setAutoDraw"):
        thisComponent.setAutoDraw(False)
thisExp.addData('WaitMessage.started', WaitMessage.tStartRefresh)
thisExp.addData('WaitMessage.stopped', WaitMessage.tStopRefresh)

# set up handler to look after randomisation of conditions etc
trials = data.TrialHandler(nReps=150.0, method='sequential', 
    extraInfo=expInfo, originPath=-1,
    trialList=[None],
    seed=None, name='trials')
thisExp.addLoop(trials)  # add the loop to the experiment
thisTrial = trials.trialList[0]  # so we can initialise stimuli with some values
# abbreviate parameter names if possible (e.g. rgb = thisTrial.rgb)
if thisTrial != None:
    for paramName in thisTrial:
        exec('{} = thisTrial[paramName]'.format(paramName))

for thisTrial in trials:
    currentLoop = trials
    # abbreviate parameter names if possible (e.g. rgb = thisTrial.rgb)
    if thisTrial != None:
        for paramName in thisTrial:
            exec('{} = thisTrial[paramName]'.format(paramName))
    
    # ------Prepare to start Routine "trial"-------
    continueRoutine = True
    routineTimer.add(1.00)
    # update component parameters for each repeat
    # keep track of which components have finished
    trialComponents = [light, dark]
    for thisComponent in trialComponents:
        thisComponent.tStart = None
        thisComponent.tStop = None
        thisComponent.tStartRefresh = None
        thisComponent.tStopRefresh = None
        if hasattr(thisComponent, 'status'):
            thisComponent.status = NOT_STARTED
    # reset timers
    t = 0
    _timeToFirstFrame = win.getFutureFlipTime(clock="now")
    trialClock.reset(-_timeToFirstFrame)  # t0 is time of first possible flip
    frameN = -1
    
    # -------Run Routine "trial"-------
    while continueRoutine and routineTimer.getTime() > 0:
        # get current time
        t = trialClock.getTime()
        tThisFlip = win.getFutureFlipTime(clock=trialClock)
        tThisFlipGlobal = win.getFutureFlipTime(clock=None)
        frameN = frameN + 1  # number of completed frames (so 0 is the first frame)
        # update/draw components on each frame
        
        # *light* updates
        if light.status == NOT_STARTED and tThisFlip >= 0.0-frameTolerance:
            # keep track of start time/frame for later
            light.frameNStart = frameN  # exact frame index
            light.tStart = t  # local t and not account for scr refresh
            light.tStartRefresh = tThisFlipGlobal  # on global time
            win.timeOnFlip(light, 'tStartRefresh')  # time at next scr refresh
            ns.send_event(
#                start=(time.time() - ns_t),
                event_type='STM+',
                label='FLIP',
            )
#            ns.resync()
            light.setAutoDraw(True)

        if light.status == STARTED:
            # is it time to stop? (based on global clock, using actual start)
            if tThisFlipGlobal > light.tStartRefresh + 0.5-frameTolerance:
                # keep track of stop time/frame for later
                light.tStop = t  # not accounting for scr refresh
                light.frameNStop = frameN  # exact frame index
                win.callOnFlip(
                    ns.send_event,
                    start=(time.time() - ns_t),
                    event_type='STM+',
                    label='FLIP',
                )
                win.timeOnFlip(light, 'tStopRefresh')  # time at next scr refresh
                light.setAutoDraw(False)
        
        # *dark* updates
        if dark.status == NOT_STARTED and tThisFlip >= 0.5-frameTolerance:
            # keep track of start time/frame for later
            dark.frameNStart = frameN  # exact frame index
            dark.tStart = t  # local t and not account for scr refresh
            dark.tStartRefresh = tThisFlipGlobal  # on global time
            win.timeOnFlip(dark, 'tStartRefresh')  # time at next scr refresh
            dark.setAutoDraw(True)
        if dark.status == STARTED:
            # is it time to stop? (based on global clock, using actual start)
            if tThisFlipGlobal > dark.tStartRefresh + 0.5 -frameTolerance:
                # keep track of stop time/frame for later
                dark.tStop = t  # not accounting for scr refresh
                dark.frameNStop = frameN  # exact frame index
                win.timeOnFlip(dark, 'tStopRefresh')  # time at next scr refresh
                dark.setAutoDraw(False)
        
        # check for quit (typically the Esc key)
        if endExpNow or defaultKeyboard.getKeys(keyList=["escape"]):
            core.quit()
        
        # check if all components have finished
        if not continueRoutine:  # a component has requested a forced-end of Routine
            break
        continueRoutine = False  # will revert to True if at least one component still running
        for thisComponent in trialComponents:
            if hasattr(thisComponent, "status") and thisComponent.status != FINISHED:
                continueRoutine = True
                break  # at least one component has not yet finished
        
        # refresh the screen
        if continueRoutine:  # don't flip if this routine is over or we'll get a blank screen
            win.flip()
    
    # -------Ending Routine "trial"-------
    for thisComponent in trialComponents:
        if hasattr(thisComponent, "setAutoDraw"):
            thisComponent.setAutoDraw(False)
    trials.addData('light.started', light.tStartRefresh)
    trials.addData('light.stopped', light.tStopRefresh)
    trials.addData('dark.started', dark.tStartRefresh)
    trials.addData('dark.stopped', dark.tStopRefresh)
    thisExp.nextEntry()
    
# completed 100.0 repeats of 'trials'


# Flip one final time so any remaining win.callOnFlip() 
# and win.timeOnFlip() tasks get executed before quitting
win.flip()

# Close NS
ns.end_rec()
ns.disconnect()

# these shouldn't be strictly necessary (should auto-save)
thisExp.saveAsWideText(filename+'.csv', delim='auto')
thisExp.saveAsPickle(filename)
logging.flush()
# make sure everything is closed down
thisExp.abort()  # or data files will save again on exit
win.close()
core.quit()
