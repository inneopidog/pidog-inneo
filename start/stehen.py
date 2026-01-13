#!/usr/bin/env python3
# stehen.py

import time

# Smooth stand
STAND_SPEED = 35
STAND_FROM_SIT_SPEED_1 = 22
STAND_FROM_SIT_SPEED_2 = 32
STAND_FROM_SIT_PAUSE = 0.25

# Head suppression window after body action
HEAD_SUPPRESS_AFTER_BODY_ACTION = 0.80


def _head_suppress(dog, seconds: float):
    fn = getattr(dog, "_head_suppress", None)
    if callable(fn):
        try:
            fn(seconds)
        except Exception:
            pass


def run(dog, stop_evt=None, from_sit: bool = False):
    """
    Stand up smoothly.
    from_sit=True uses two-phase stand for smoother transition.
    """
    _head_suppress(dog, HEAD_SUPPRESS_AFTER_BODY_ACTION)

    if from_sit:
        dog.do_action("stand", speed=STAND_FROM_SIT_SPEED_1)
        dog.wait_all_done()
        time.sleep(STAND_FROM_SIT_PAUSE)

        dog.do_action("stand", speed=STAND_FROM_SIT_SPEED_2)
        dog.wait_all_done()
    else:
        dog.do_action("stand", speed=STAND_SPEED)
        dog.wait_all_done()
