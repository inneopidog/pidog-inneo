#!/usr/bin/env python3
# start/sitzen.py

import threading
import time

# Smooth sit (two-phase)
SIT_SPEED_1 = 28
SIT_SPEED_2 = 38
SIT_PAUSE = 0.20

# Head scan speed feel (DEINE WERTE)
SCAN_STEP_SPEED = 150
SCAN_HOLD = 4.5
SCAN_LOOP_SLEEP = 0.2

# How much the head moves while "looking around" (DEINE WERTE)
YAW_LEFT_RIGHT = 40
PITCH_UP = 15
PITCH_DOWN = -20
ROLL_TILT = 15

# Head suppression window during sit to avoid jitter from other modules (DEINE WERTE)
HEAD_SUPPRESS_AFTER_BODY_ACTION_SIT = 2.5

# Axis mapping
YAW_IDX = 0
PITCH_IDX = 1
ROLL_IDX = None  # bei dir scheint die 3. Achse nichts zu machen

# NEW: leicht nach vorne neigen, bevor er scannt
HEAD_FORWARD_TILT = -30      # negativer Wert = nach vorne/unten (wenn bei dir andersrum: +12)
HEAD_FORWARD_SPEED = 25
HEAD_FORWARD_SETTLE = 0.25   # kurze Pause, damit man es sieht


def _head_suppress(dog, seconds: float):
    fn = getattr(dog, "_head_suppress", None)
    if callable(fn):
        try:
            fn(seconds)
        except Exception:
            pass


def _get_head(dog):
    return getattr(dog, "head", None)


def _read_head_pose(dog):
    h = _get_head(dog)
    if h is None:
        return [0, 0, 0]

    try:
        sp = getattr(h, "servo_positions", None)
        if isinstance(sp, (list, tuple)) and len(sp) >= 3:
            return [int(sp[0]), int(sp[1]), int(sp[2])]
    except Exception:
        pass

    return [0, 0, 0]


def _head_move(dog, pose, speed=SCAN_STEP_SPEED):
    h = _get_head(dog)
    if h is None or not hasattr(h, "servo_move"):
        return

    try:
        h.servo_move(list(pose), speed=speed)
    except TypeError:
        h.servo_move(list(pose))
    except Exception:
        pass


def _ensure_scan_state(dog):
    if not hasattr(dog, "_sit_scan_lock"):
        dog._sit_scan_lock = threading.Lock()
    if not hasattr(dog, "_sit_scan_stop"):
        dog._sit_scan_stop = threading.Event()
    if not hasattr(dog, "_sit_scan_thread"):
        dog._sit_scan_thread = None
    if not hasattr(dog, "_sit_scan_home"):
        dog._sit_scan_home = [0, 0, 0]


def start_scan(dog, global_stop_evt=None):
    _ensure_scan_state(dog)

    with dog._sit_scan_lock:
        if dog._sit_scan_thread and dog._sit_scan_thread.is_alive():
            return

        dog._sit_scan_stop.clear()

        def worker():
            _head_suppress(dog, 0.0)

            # Home wurde in run() VOR dem Sit gespeichert (kalibrierte Normalhaltung)
            home = list(getattr(dog, "_sit_scan_home", [0, 0, 0]))

            # 1) Sichtbar auf Home zentrieren
            _head_move(dog, home, speed=35)
            time.sleep(0.20)

            # 2) Jetzt leicht nach vorne neigen (Pitch)
            base = home[:]
            if PITCH_IDX is not None and PITCH_IDX < len(base):
                base[PITCH_IDX] = base[PITCH_IDX] + HEAD_FORWARD_TILT

            _head_move(dog, base, speed=HEAD_FORWARD_SPEED)
            time.sleep(HEAD_FORWARD_SETTLE)

            # Ab hier scannt er um "base" (Home + Forward Tilt)
            def make_pose(yaw_delta=0, pitch_delta=0, roll_delta=0):
                p = base[:]
                if YAW_IDX is not None and YAW_IDX < len(p):
                    p[YAW_IDX] = p[YAW_IDX] + yaw_delta
                if PITCH_IDX is not None and PITCH_IDX < len(p):
                    p[PITCH_IDX] = p[PITCH_IDX] + pitch_delta
                if ROLL_IDX is not None and ROLL_IDX < len(p):
                    p[ROLL_IDX] = p[ROLL_IDX] + roll_delta
                return p

            poses = [
                make_pose(yaw_delta=-YAW_LEFT_RIGHT),
                make_pose(yaw_delta=+YAW_LEFT_RIGHT),
                make_pose(pitch_delta=+PITCH_UP),
                make_pose(pitch_delta=+PITCH_DOWN),
                make_pose(yaw_delta=-12, pitch_delta=+4, roll_delta=+ROLL_TILT),
                make_pose(yaw_delta=+12, pitch_delta=+4, roll_delta=-ROLL_TILT),
                make_pose(),
            ]

            i = 0
            while True:
                if global_stop_evt is not None and global_stop_evt.is_set():
                    break
                if dog._sit_scan_stop.is_set():
                    break

                _head_move(dog, poses[i % len(poses)], speed=SCAN_STEP_SPEED)

                t_end = time.time() + SCAN_HOLD
                while time.time() < t_end:
                    if global_stop_evt is not None and global_stop_evt.is_set():
                        dog._sit_scan_stop.set()
                        break
                    if dog._sit_scan_stop.is_set():
                        break
                    time.sleep(SCAN_LOOP_SLEEP)

                i += 1

            # return to "home" at end (nicht base), damit er wieder neutral ist
            _head_suppress(dog, 0.0)
            _head_move(dog, home, speed=35)

        dog._sit_scan_thread = threading.Thread(target=worker, daemon=True)
        dog._sit_scan_thread.start()


def stop_scan(dog):
    if not hasattr(dog, "_sit_scan_stop"):
        return

    try:
        dog._sit_scan_stop.set()
    except Exception:
        pass

    try:
        _head_suppress(dog, 0.0)
        home = getattr(dog, "_sit_scan_home", [0, 0, 0])
        _head_move(dog, home, speed=35)
    except Exception:
        pass


def run(dog, stop_evt=None):
    stop_scan(dog)

    # Home-Pose IMMER VOR dem Sit speichern (kalibrierte Normalhaltung)
    dog._sit_scan_home = _read_head_pose(dog)

    _head_suppress(dog, HEAD_SUPPRESS_AFTER_BODY_ACTION_SIT)

    dog.do_action("sit", speed=SIT_SPEED_1)
    dog.wait_all_done()

    _head_suppress(dog, HEAD_SUPPRESS_AFTER_BODY_ACTION_SIT)
    time.sleep(SIT_PAUSE)

    _head_suppress(dog, HEAD_SUPPRESS_AFTER_BODY_ACTION_SIT)
    dog.do_action("sit", speed=SIT_SPEED_2)
    dog.wait_all_done()

    start_scan(dog, global_stop_evt=stop_evt)
