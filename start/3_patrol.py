#!/usr/bin/env python3
import time
from pidog.preset_actions import bark

# ---------------- Tuning ----------------
DANGER_DISTANCE = 15

# Wie schnell/kräftig laufen soll (je nach Firmware fühlt sich 40–70 gut an)
WALK_SPEED = 55

# Tick-Mode: wie oft wir Bewegung "nachkicken" (kleiner = flüssiger, aber mehr CPU)
WALK_TICK_SEC = 0.08

# Wenn dein Dog sonst zu hektisch ist: 0.10–0.15
# Wenn er träge ist: 0.05–0.08
# ----------------------------------------


def _call_do_action(dog, name, speed):
    try:
        dog.do_action(name, speed=speed)
        return True
    except Exception:
        return False


def _try_start_continuous_walk(dog, speed):
    """
    Ziel: Eine Walk-Action starten, die selbständig weiterläuft,
    bis body_stop() oder eine andere Action kommt.

    Falls eure Firmware das nicht unterstützt, gibt es False zurück.
    """
    # Häufige Namen in Beispielen/Builds
    for action in ("walk", "forward", "walk_forward", "go"):
        if _call_do_action(dog, action, speed):
            return True
    return False


def _try_walk_tick(dog, speed):
    """
    Tick-basierter Walk (für Firmwares ohne continuous walk).
    Probiert mehrere APIs.
    """
    # 1) Action-Impulse
    for action in ("forward", "walk_forward", "walk", "go"):
        if _call_do_action(dog, action, speed):
            return True

    # 2) Direkte Methoden
    for method_name in ("forward", "walk", "move_forward", "go_forward", "body_forward"):
        fn = getattr(dog, method_name, None)
        if callable(fn):
            try:
                fn(speed)
                return True
            except Exception:
                pass

    # 3) body_move(vx, vy, yaw) falls vorhanden
    fn = getattr(dog, "body_move", None)
    if callable(fn):
        try:
            # etwas stärker nach vorne als vorher
            fn(35, 0, 0)
            return True
        except Exception:
            pass

    return False


def run(dog, stop_evt=None):
    def should_stop() -> bool:
        return (stop_evt is not None) and stop_evt.is_set()

    # Startpose
    try:
        dog.do_action("stand", speed=80)
        dog.wait_all_done()
        time.sleep(0.3)
    except Exception:
        pass

    stand = dog.legs_angle_calculation([[0, 80], [0, 80], [30, 75], [30, 75]])

    walking_mode = "none"   # "continuous" | "tick" | "none"
    last_kick = 0.0

    while True:
        if should_stop():
            break

        distance = round(dog.read_distance(), 2)
        print(f"distance: {distance} cm", end="", flush=True)

        # ---------------- DANGER ----------------
        if distance > 0 and distance < DANGER_DISTANCE:
            print("\033[0;31m DANGER !\033[m")

            # Stop immediately
            try:
                dog.body_stop()
            except Exception:
                pass

            walking_mode = "none"
            last_kick = 0.0

            head_yaw = dog.head_current_angles[0]

            try:
                dog.rgb_strip.set_mode("bark", "red", bps=2)
            except Exception:
                pass

            try:
                dog.tail_move([[0]], speed=80)
                dog.legs_move([stand], speed=70)
                dog.wait_all_done()
            except Exception:
                pass

            time.sleep(0.25)

            try:
                bark(dog, [head_yaw, 0, 0])
            except Exception:
                pass

            # Wait until safe (or stop)
            while True:
                if should_stop():
                    break

                distance = round(dog.read_distance(), 2)
                if distance > 0 and distance < DANGER_DISTANCE:
                    print(f"distance: {distance} cm \033[0;31m DANGER !\033[m")
                else:
                    print(f"distance: {distance} cm", end="", flush=True)
                    break
                time.sleep(0.02)

            print("", flush=True)

        # ---------------- SAFE ----------------
        else:
            print("", flush=True)

            now = time.time()

            # Wenn wir noch nicht laufen: erst versuchen "continuous"
            if walking_mode == "none":
                ok = _try_start_continuous_walk(dog, WALK_SPEED)
                if ok:
                    walking_mode = "continuous"
                    # Bei continuous müssen wir nichts mehr tickern
                else:
                    walking_mode = "tick"
                    last_kick = 0.0  # sofort los-tickern

            # Tick-Mode: regelmäßig nachkicken -> flüssiger
            if walking_mode == "tick":
                if now - last_kick >= WALK_TICK_SEC:
                    ok = _try_walk_tick(dog, WALK_SPEED)
                    if not ok:
                        # wenn gar nichts passt, abbrechen
                        print("[WARN] Walk API nicht gefunden. Passe _try_walk_tick an.", flush=True)
                        walking_mode = "none"
                        # nicht spammen
                        time.sleep(0.5)
                    last_kick = now

        time.sleep(0.01)

    # Shutdown
    try:
        dog.body_stop()
    except Exception:
        pass
