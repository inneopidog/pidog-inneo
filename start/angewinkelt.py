#!/usr/bin/env python3
import time
import statistics

# ---------- Einstellungen ----------
HAND_MIN = 10.0
HAND_MAX = 20.0

SPEED_LEGS = 25

# Smooth sit (gleich wie im Hotloop)
SIT_SPEED_1 = 28
SIT_SPEED_2 = 38
SIT_PAUSE = 0.20

# Vorderes linkes Bein (Hundesicht)
FL0, FL1 = 0, 1

# Hinteres linkes Bein (Hundesicht)
BL0, BL1 = 4, 5

# Pfötchen-Feintuning
PAW_DELTA = -60       # nach vorne strecken
PAW_ANGLE = -30       # Fuß anwinkeln vorne
BACK_SUPPORT = 5      # hinten links unterer Servo stützen
# ----------------------------------


def get_distance_filtered(dog, samples=7, delay=0.05):
    vals = []
    for _ in range(samples):
        d = float(dog.read_distance())
        if 0 < d < 250:
            vals.append(d)
        time.sleep(delay)
    if not vals:
        return None
    return statistics.median(vals)


def move_legs(dog, pose, pause=0.8):
    dog.legs.servo_move(pose, speed=SPEED_LEGS)
    time.sleep(pause)


def safe_sit(dog):
    dog.do_action("sit", speed=SIT_SPEED_1)
    dog.wait_all_done()
    time.sleep(SIT_PAUSE)

    dog.do_action("sit", speed=SIT_SPEED_2)
    dog.wait_all_done()


def give_paw_sitting(dog):
    # Sitz-Pose sichern
    sit_pose = list(dog.legs.servo_positions)
    t = sit_pose.copy()

    # vorne links: nach vorne strecken
    t[FL0] = sit_pose[FL0] + PAW_DELTA

    # vorne links: Fuß anwinkeln
    t[FL1] = sit_pose[FL1] + PAW_ANGLE

    # hinten links: unteres Gelenk stützen
    t[BL1] = sit_pose[BL1] + BACK_SUPPORT

    move_legs(dog, t, pause=1.3)
    time.sleep(1.0)

    # zurück in Sitz
    move_legs(dog, sit_pose, pause=1.0)


def wait_for_hand(dog, timeout=10.0, stop_evt=None):
    print(f"Hand {HAND_MIN:.0f}–{HAND_MAX:.0f} cm vor Sensor halten ...")
    end = time.time() + timeout

    while time.time() < end:
        if stop_evt is not None and stop_evt.is_set():
            return False

        d = get_distance_filtered(dog)
        if d is None:
            continue

        print(f"Distanz: {d:.1f} cm")
        if HAND_MIN <= d <= HAND_MAX:
            return True

        time.sleep(0.1)

    return False


def run(dog, stop_evt=None):
    # Erst langsam hinsetzen (einheitlich)
    safe_sit(dog)

    ok = wait_for_hand(dog, timeout=10.0, stop_evt=stop_evt)
    if ok and not (stop_evt is not None and stop_evt.is_set()):
        print("Pfötchen!")
        give_paw_sitting(dog)
    else:
        print("Keine Hand erkannt oder abgebrochen.")
