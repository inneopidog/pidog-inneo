#!/usr/bin/env python3
# 3_patrol.py
import time

# Bark ist optional (Sound braucht oft sudo)
try:
    from pidog.preset_actions import bark as _bark
except Exception:
    _bark = None

# ---------------- Tuning ----------------
DANGER_DISTANCE = 18        # etwas höher = bremst früher
FORWARD_SPEED   = 98        # deutlich schneller
FORWARD_STEPS   = 2         # weniger Drift als 4+, trotzdem schnell
TICK_SEC        = 0.05      # öfter nachschieben = flüssiger

# Status-Ausgabe drosseln (gegen Spam)
STATUS_MIN_INTERVAL = 0.25
STATUS_MIN_DELTA    = 1.0

# Sound/Bark aktiv?
ENABLE_BARK = False  # True nur, wenn du via sudo startest
# ----------------------------------------


def _do_action(dog, name, *, speed=50, step_count=1) -> bool:
    try:
        dog.do_action(name, step_count=step_count, speed=speed)
        return True
    except Exception:
        return False


def read_distance_best_effort(dog) -> float:
    """
    Versucht mehrere bekannte APIs (je nach PiDog Library/Firmware).
    Gibt -1.0 zurück, wenn nichts Valides kommt.
    """
    # 1) manche Builds haben dog.read_distance()
    fn = getattr(dog, "read_distance", None)
    if callable(fn):
        try:
            d = float(fn())
            if d > 0:
                return d
        except Exception:
            pass

    # 2) andere haben dog.ultrasonic.*
    us = getattr(dog, "ultrasonic", None)
    if us is not None:
        for name in ("read_distance", "read_distance_value", "read_distance_cm", "get_distance"):
            f = getattr(us, name, None)
            if callable(f):
                try:
                    d = float(f())
                    if d > 0:
                        return d
                except Exception:
                    pass

    return -1.0


def run(dog, stop_evt=None):
    def should_stop() -> bool:
        return (stop_evt is not None) and stop_evt.is_set()

    def hard_stop():
        # Sofort alles anhalten (räumt Action-Queues)
        try:
            dog.body_stop()
        except Exception:
            pass

    def status_line(text: str):
        txt = (text[:120]).ljust(120)
        print("\r" + txt, end="", flush=True)

    def status_newline(text: str):
        print("\r" + text + " " * 10, flush=True)

    # Startpose
    try:
        dog.do_action("stand", speed=80)
        dog.wait_all_done()
        time.sleep(0.3)
    except Exception:
        pass

    # Stand-Pose (wie vorher)
    try:
        stand = dog.legs_angle_calculation([[0, 80], [0, 80], [30, 75], [30, 75]])
    except Exception:
        stand = None

    last_tick = 0.0
    last_status_ts = 0.0
    last_dist = None

    while True:
        # Stop immer priorisieren
        if should_stop():
            hard_stop()
            break

        # Distanz best-effort lesen
        distance = round(read_distance_best_effort(dog), 2)
        now = time.time()

        # Status throttling
        dist_changed = (
            last_dist is None or
            (distance > 0 and last_dist is not None and abs(distance - last_dist) >= STATUS_MIN_DELTA)
        )
        if (now - last_status_ts) >= STATUS_MIN_INTERVAL or dist_changed:
            status_line(f"[WALK] distance: {distance:.2f} cm")
            last_status_ts = now
            last_dist = distance

        # Wenn Distanz ungültig: NICHT blind weiterlaufen -> sofort stoppen
        if distance <= 0:
            hard_stop()
            status_line("[WALK] distance invalid -> STOP (check ultrasonic / API)")
            time.sleep(0.10)
            continue

        # ---------------- DANGER ----------------
        if distance < DANGER_DISTANCE:
            status_newline(f"[WALK] distance: {distance:.2f} cm  DANGER!")
            hard_stop()

            if should_stop():
                break

            # LED/pose
            try:
                dog.rgb_strip.set_mode("bark", "red", bps=2)
            except Exception:
                pass

            if stand is not None and not should_stop():
                try:
                    dog.tail_move([[0]], speed=80)
                    dog.legs_move([stand], speed=70)
                    dog.wait_all_done()
                except Exception:
                    pass

            if should_stop():
                hard_stop()
                break

            # Bark optional
            if ENABLE_BARK and _bark is not None and not should_stop():
                try:
                    head_yaw = dog.head_current_angles[0]
                except Exception:
                    head_yaw = 0
                try:
                    _bark(dog, [head_yaw, 0, 0])
                except Exception:
                    pass

            # Warten bis wieder safe (abbrechbar)
            while True:
                if should_stop():
                    hard_stop()
                    break

                distance = round(read_distance_best_effort(dog), 2)
                if distance <= 0:
                    hard_stop()
                    status_line("[WALK] distance invalid -> STOP (waiting)")
                    time.sleep(0.10)
                    continue

                if distance < DANGER_DISTANCE:
                    status_line(f"[WALK] distance: {distance:.2f} cm  DANGER! (waiting)")
                    time.sleep(0.05)
                else:
                    status_newline(f"[WALK] safe again (distance: {distance:.2f} cm)")
                    break

        # ---------------- SAFE ----------------
        else:
            if should_stop():
                hard_stop()
                break

            # optional LED
            try:
                dog.rgb_strip.set_mode("breath", "white", bps=0.5)
            except Exception:
                pass

            # laufen in Ticks
            if now - last_tick >= TICK_SEC:
                if should_stop():
                    hard_stop()
                    break

                ok = _do_action(dog, "forward", speed=FORWARD_SPEED, step_count=FORWARD_STEPS)
                if not ok:
                    status_newline("[ERROR] Preset action 'forward' nicht verfügbar. Prüfe PiDog Library/Version.")
                    time.sleep(0.5)

                # falls stop in der Zwischenzeit kam
                if should_stop():
                    hard_stop()
                    break

                last_tick = now

        time.sleep(0.01)

    status_newline("[WALK] stopping...")
    hard_stop()
