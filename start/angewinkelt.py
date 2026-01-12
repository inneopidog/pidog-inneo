#!/usr/bin/env python3
import time
import statistics

# ---------- Einstellungen ----------
HAND_MIN = 10.0
HAND_MAX = 20.0

SPEED_LEGS = 25

# Smooth sit
SIT_SPEED_1 = 28
SIT_SPEED_2 = 38
SIT_PAUSE = 0.20

# Vorderes linkes Bein (Hundesicht)
FL0, FL1 = 0, 1

# Hinteres linkes Bein (Hundesicht)
BL0, BL1 = 4, 5

# Pfötchen-Feintuning
PAW_DELTA = -60
PAW_ANGLE = -30
BACK_SUPPORT = 5

# ---------- Kopfsteuerung (GETESTET) ----------
PITCH_SERVO_INDEX = 1         # Servo 1 = hoch/runter
HEAD_PITCH_DELTA_DOWN = -60   # stark nach unten schauen
HEAD_TILT_SPEED = 35
HEAD_TILT_PAUSE = 0.25
# -------------------------------------------


def get_distance_filtered(dog, samples=7, delay=0.05):
    vals = []
    for _ in range(samples):
        d = float(dog.read_distance())
        if 0 < d < 250:
            vals.append(d)
        time.sleep(delay)
    return statistics.median(vals) if vals else None


def move_legs(dog, pose, pause=0.8):
    dog.legs.servo_move(pose, speed=SPEED_LEGS)
    time.sleep(pause)


def safe_sit(dog):
    dog.do_action("sit", speed=SIT_SPEED_1)
    dog.wait_all_done()
    time.sleep(SIT_PAUSE)

    dog.do_action("sit", speed=SIT_SPEED_2)
    dog.wait_all_done()


# ---------- Kopfsteuerung ----------
def head_get_positions(dog):
    h = getattr(dog, "head", None)
    if h is None:
        return None, None
    try:
        pos = list(h.servo_positions)
    except Exception:
        pos = [0, 0, 0]
    return h, pos


def head_move_pose(dog, pose, speed=35):
    h = getattr(dog, "head", None)
    if h is None:
        return False
    try:
        h.servo_move(pose, speed=speed)
    except TypeError:
        h.servo_move(pose)

    try:
        dog.wait_all_done()
    except Exception:
        pass
    return True


def head_pitch_relative(neutral_pose, delta):
    pose = neutral_pose.copy()
    pose[PITCH_SERVO_INDEX] = neutral_pose[PITCH_SERVO_INDEX] + delta
    return pose
# --------------------------------


def give_paw_sitting(dog):
    sit_pose = list(dog.legs.servo_positions)
    t = sit_pose.copy()

    t[FL0] = sit_pose[FL0] + PAW_DELTA
    t[FL1] = sit_pose[FL1] + PAW_ANGLE
    t[BL1] = sit_pose[BL1] + BACK_SUPPORT

    move_legs(dog, t, pause=1.3)
    time.sleep(1.0)

    move_legs(dog, sit_pose, pause=1.0)


def wait_for_hand(dog, timeout=10.0, stop_evt=None):
    print("Hand 10–20 cm vor Sensor halten ...")
    end = time.time() + timeout

    while time.time() < end:
        if stop_evt and stop_evt.is_set():
            return False

        d = get_distance_filtered(dog)
        if d is not None:
            print(f"Distanz: {d:.1f} cm")
            if HAND_MIN <= d <= HAND_MAX:
                return True

        time.sleep(0.1)

    return False


def run(dog, stop_evt=None):
    # 1) Hinsetzen
    safe_sit(dog)

    # 2) Neutral-Kopfpose MERKEN (damit wir später wirklich zurück fahren)
    h, neutral_head = head_get_positions(dog)
    if h is None or neutral_head is None:
        print("[WARN] head not available, continuing without head movement")
        neutral_head = None

    # 3) Kopf nach unten (relativ zur gemerkten Neutralpose)
    if neutral_head is not None:
        down_pose = head_pitch_relative(neutral_head, HEAD_PITCH_DELTA_DOWN)
        head_move_pose(dog, down_pose, speed=HEAD_TILT_SPEED)
        time.sleep(HEAD_TILT_PAUSE)

    # 4) Warten auf Hand
    ok = wait_for_hand(dog, timeout=10.0, stop_evt=stop_evt)

    if ok and not (stop_evt and stop_evt.is_set()):
        # 5) SOFORT Kopf wieder normal (genau Neutralpose)
        if neutral_head is not None:
            head_move_pose(dog, neutral_head, speed=HEAD_TILT_SPEED)
            time.sleep(HEAD_TILT_PAUSE)

        print("Pfötchen!")
        give_paw_sitting(dog)
    else:
        print("Keine Hand erkannt.")
        # auch bei Abbruch Kopf zurück
        if neutral_head is not None:
            head_move_pose(dog, neutral_head, speed=HEAD_TILT_SPEED)
            time.sleep(HEAD_TILT_PAUSE)
