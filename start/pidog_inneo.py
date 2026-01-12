#!/usr/bin/env python3
# pidog_inneo.py
import os
import sys
import threading
import queue
import importlib.util
import traceback
import time

from pidog import Pidog

# ----------------- CONFIG -----------------
DEFAULT_SPEED = 60

# Smooth stand
STAND_SPEED = 35
STAND_FROM_SIT_SPEED_1 = 22
STAND_FROM_SIT_SPEED_2 = 32
STAND_FROM_SIT_PAUSE = 0.25

# Smooth sit + lie down (two-phase for consistency)
SIT_SPEED_1 = 28
SIT_SPEED_2 = 38
SIT_PAUSE = 0.20

LIE_SPEED_1 = 28
LIE_SPEED_2 = 38
LIE_PAUSE = 0.20

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PATH_PATROL = os.path.join(BASE_DIR, "3_patrol.py")
PATH_PAW    = os.path.join(BASE_DIR, "angewinkelt.py")
PATH_LIE    = os.path.join(BASE_DIR, "hinlegen.py")

# Head anti-jitter (global) - improved
HEAD_DEADBAND = 4           # ignore <= 4 steps difference vs last sent
HEAD_MIN_INTERVAL = 0.10    # 100ms max head command rate

# Extra suppression for sit (because jitter happens there)
HEAD_SUPPRESS_AFTER_BODY_ACTION = 0.80
HEAD_SUPPRESS_AFTER_BODY_ACTION_SIT = 2.2

# Sit head tilt: absolute target pose during sit (as requested)
SIT_HEAD_TILT_POSE = [0, 0, -20]
SIT_HEAD_TILT_SPEED = 25
# ------------------------------------------

# Touch -> tail wag
TOUCH_POLL_INTERVAL = 0.03
TOUCH_COOLDOWN = 0.8
TAIL_WAG_DURATION = 1.2
TAIL_WAG_SPEED = 60
# ------------------------------------------

cmd_q = queue.Queue()
stop_event = threading.Event()


def load_module_from_path(name: str, path: str):
    print(f"[DEBUG] Loading module {name} from: {path}", flush=True)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module from: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print(f"[DEBUG] Module loaded: {name}", flush=True)
    return mod


def stdin_reader():
    """
    Kein Prompt, damit Walk-Statuszeile nicht ständig den Prompt "zerstört".
    """
    while not stop_event.is_set():
        try:
            s = input().strip()
        except (EOFError, KeyboardInterrupt):
            stop_event.set()
            break
        if s:
            cmd_q.put(s)


def call_best_entry(mod, dog: Pidog, stop_evt=None):
    candidates = [
        ("run",  (dog, stop_evt)),
        ("run",  (dog,)),
        ("main", (dog, stop_evt)),
        ("main", (dog,)),
    ]
    for fn_name, args in candidates:
        fn = getattr(mod, fn_name, None)
        if callable(fn):
            real_args = tuple(a for a in args if a is not None)
            return fn(*real_args)
    raise RuntimeError("No run(...) or main(...) found in module.")


# ----------------- Head anti-jitter patch (coalesce + suppression) -----------------
_head_lock = threading.Lock()
_head_last_sent_pose = None
_head_last_sent_t = 0.0
_head_pending_pose = None
_head_pending_speed = None
_head_sender_started = False
_head_suppress_until = 0.0


def head_suppress(seconds: float):
    """Block head commands for a short window (used during sit/stand/lie transitions)."""
    global _head_suppress_until
    with _head_lock:
        _head_suppress_until = max(_head_suppress_until, time.time() + float(seconds))


def _patch_head_servo_move(dog: Pidog):
    """
    Patches dog.head.servo_move(...) to reduce micro jitter globally:
    - deadband vs last SENT pose (stable reference)
    - coalesces rapid updates (latest-wins) via sender thread
    - rate-limits actual hardware commands
    - allows explicit suppression windows during body transitions
    """
    global _head_sender_started

    h = getattr(dog, "head", None)
    if h is None or not hasattr(h, "servo_move") or not callable(h.servo_move):
        print("[WARN] head servo_move not available; anti-jitter patch skipped", flush=True)
        return

    orig = h.servo_move  # original method

    def _sender():
        global _head_pending_pose, _head_pending_speed, _head_last_sent_pose, _head_last_sent_t
        while not stop_event.is_set():
            now = time.time()
            pose = None
            spd = None

            with _head_lock:
                # suppression window: do not send while suppressed
                if now >= _head_suppress_until:
                    if _head_pending_pose is not None and (now - _head_last_sent_t) >= HEAD_MIN_INTERVAL:
                        pose = _head_pending_pose
                        spd = _head_pending_speed
                        _head_pending_pose = None
                        _head_pending_speed = None

            if pose is not None:
                try:
                    if spd is None:
                        orig(pose)
                    else:
                        try:
                            orig(pose, speed=spd)
                        except TypeError:
                            orig(pose)
                except Exception:
                    pass

                with _head_lock:
                    _head_last_sent_pose = list(pose)
                    _head_last_sent_t = time.time()

            time.sleep(0.01)

    def filtered_servo_move(pose, speed=None, **kwargs):
        global _head_pending_pose, _head_pending_speed, _head_last_sent_pose

        try:
            target = list(pose)
        except Exception:
            return orig(pose)

        now = time.time()
        with _head_lock:
            # suppression: ignore head updates entirely during suppression
            if now < _head_suppress_until:
                return

            # Skip exact duplicates vs last sent
            if _head_last_sent_pose is not None and len(_head_last_sent_pose) == len(target):
                if all(target[i] == _head_last_sent_pose[i] for i in range(len(target))):
                    return

                # Deadband vs last sent
                if all(abs(target[i] - _head_last_sent_pose[i]) <= HEAD_DEADBAND for i in range(len(target))):
                    return

            # Coalesce: keep only the latest request
            _head_pending_pose = target
            _head_pending_speed = speed

        # ignore kwargs intentionally

    h.servo_move = filtered_servo_move

    if not _head_sender_started:
        _head_sender_started = True
        threading.Thread(target=_sender, daemon=True).start()

    print("[INFO] Head anti-jitter patch enabled (coalesce + deadband + suppression)", flush=True)


# ----------------- Body actions (with head suppression) -----------------
def safe_stand(dog: Pidog, from_sit: bool):
    head_suppress(HEAD_SUPPRESS_AFTER_BODY_ACTION)

    if from_sit:
        dog.do_action("stand", speed=STAND_FROM_SIT_SPEED_1)
        dog.wait_all_done()
        time.sleep(STAND_FROM_SIT_PAUSE)

        dog.do_action("stand", speed=STAND_FROM_SIT_SPEED_2)
        dog.wait_all_done()
    else:
        dog.do_action("stand", speed=STAND_SPEED)
        dog.wait_all_done()


def safe_sit(dog: Pidog):
    """
    Requested behavior:
    While sitting down, tilt head to [0, 0, -20].
    We do it right before starting the sit transition and keep head suppressed
    during the entire sit sequence to avoid micro jitter updates.
    """
    h = getattr(dog, "head", None)

    # Start a longer suppression window for the whole sit transition
    head_suppress(HEAD_SUPPRESS_AFTER_BODY_ACTION_SIT)

    # Set head tilt (best-effort) just before sit begins
    if h is not None:
        try:
            # allow this one command to be sent by briefly un-suppressing, then re-suppress
            head_suppress(0.0)
            h.servo_move(SIT_HEAD_TILT_POSE, speed=SIT_HEAD_TILT_SPEED)
        except Exception:
            pass
        finally:
            head_suppress(HEAD_SUPPRESS_AFTER_BODY_ACTION_SIT)

    # Sit phase 1
    dog.do_action("sit", speed=SIT_SPEED_1)
    dog.wait_all_done()

    # Re-suppress to ensure we don't "open" between phases
    head_suppress(HEAD_SUPPRESS_AFTER_BODY_ACTION_SIT)
    time.sleep(SIT_PAUSE)

    # Sit phase 2
    head_suppress(HEAD_SUPPRESS_AFTER_BODY_ACTION_SIT)
    dog.do_action("sit", speed=SIT_SPEED_2)
    dog.wait_all_done()

    # Keep suppression a tiny bit longer after finishing
    head_suppress(0.4)


def safe_lie_down(dog: Pidog):
    head_suppress(HEAD_SUPPRESS_AFTER_BODY_ACTION)

    for action in ("lie", "lie_down", "rest"):
        try:
            dog.do_action(action, speed=LIE_SPEED_1)
            dog.wait_all_done()
            time.sleep(LIE_PAUSE)

            dog.do_action(action, speed=LIE_SPEED_2)
            dog.wait_all_done()
            return
        except Exception:
            continue

    raise RuntimeError("No supported lie-down action found (tried: lie, lie_down, rest).")


# ----------------- Touch -> tail wag -----------------
_wag_lock = threading.Lock()
_wag_running = False


def wag_tail(dog: Pidog, duration=TAIL_WAG_DURATION, speed=TAIL_WAG_SPEED):
    """
    Tries to wag the tail using actions; falls back to tail servo oscillation.
    Runs best-effort and avoids overlapping wags.
    """
    global _wag_running
    with _wag_lock:
        if _wag_running:
            return
        _wag_running = True

    try:
        # Preferred: action-based wag
        for action in ("wag_tail", "tail_wag", "wag", "happy"):
            try:
                dog.do_action(action, speed=speed)
                dog.wait_all_done()
                return
            except Exception:
                continue

        # Fallback: direct tail servo oscillation
        tail = getattr(dog, "tail", None)
        if tail is None or not hasattr(tail, "servo_move"):
            return

        try:
            base = list(tail.servo_positions)[0]
        except Exception:
            base = 0

        amp = 20
        end = time.time() + float(duration)
        sign = 1

        while time.time() < end and not stop_event.is_set():
            pos = base + sign * amp
            sign *= -1
            try:
                tail.servo_move([pos], speed=40)
            except TypeError:
                tail.servo_move([pos])
            time.sleep(0.12)

        # back to base
        try:
            tail.servo_move([base], speed=35)
        except TypeError:
            tail.servo_move([base])

    finally:
        with _wag_lock:
            _wag_running = False


def _read_touch_any(dog: Pidog) -> bool:
    """
    Best-effort touch read for 'dual_touch' / 'touch' variants.
    Returns True if any touch is detected.
    """
    dt = getattr(dog, "dual_touch", None) or getattr(dog, "touch", None)
    if dt is None:
        return False

    # common patterns
    for attr in ("read", "get_value", "value", "status", "get_status"):
        fn = getattr(dt, attr, None)
        if callable(fn):
            try:
                v = fn()
            except Exception:
                continue

            if isinstance(v, (list, tuple)):
                return any(bool(x) for x in v)
            if isinstance(v, dict):
                return any(bool(x) for x in v.values())
            try:
                return bool(v)
            except Exception:
                pass

    # sometimes dual touch exposes channel methods
    for meth in ("is_touched", "touched"):
        fn = getattr(dt, meth, None)
        if callable(fn):
            for ch in (0, 1):
                try:
                    if fn(ch):
                        return True
                except Exception:
                    continue

    return False


def start_touch_wag_watcher(dog: Pidog):
    """
    Background thread: if touch is detected, wag tail (debounced).
    """
    last_fire = 0.0
    prev_state = False

    def worker():
        nonlocal last_fire, prev_state
        while not stop_event.is_set():
            try:
                touched = _read_touch_any(dog)
            except Exception:
                touched = False

            # Rising edge + cooldown
            now = time.time()
            if touched and not prev_state and (now - last_fire) >= TOUCH_COOLDOWN:
                last_fire = now
                threading.Thread(target=wag_tail, args=(dog,), daemon=True).start()

            prev_state = touched
            time.sleep(TOUCH_POLL_INTERVAL)

    threading.Thread(target=worker, daemon=True).start()
    print("[INFO] Touch->Tail-wag watcher enabled", flush=True)


def main():
    print("[INFO] pidog_inneo.py starting...", flush=True)
    print(f"[INFO] Python: {sys.version}", flush=True)
    print(f"[INFO] BASE_DIR: {BASE_DIR}", flush=True)

    for p in (PATH_PATROL, PATH_PAW, PATH_LIE):
        if not os.path.exists(p):
            raise FileNotFoundError(f"Missing file: {p}")

    print("[INFO] Starting PiDog Hotloop", flush=True)
    print(
        "Commands:\n"
        "  sit       - sit down\n"
        "  stand     - stand up\n"
        "  lie down  - lie on the ground\n"
        "  paw       - give paw\n"
        "  walk      - start walking\n"
        "  stop      - stop walking\n"
        "  quit      - exit program\n"
        "\nTippe einfach den Command und drücke Enter.",
        flush=True
    )

    print("[DEBUG] Creating Pidog() ...", flush=True)
    dog = Pidog()
    print("[DEBUG] Pidog() created", flush=True)

    # Global head anti-jitter patch (affects all modules using dog.head.servo_move)
    _patch_head_servo_move(dog)

    # Touch switch -> tail wag (global)
    start_touch_wag_watcher(dog)

    patrol_mod = load_module_from_path("patrol_mod", PATH_PATROL)
    paw_mod    = load_module_from_path("paw_mod", PATH_PAW)
    lie_mod    = load_module_from_path("lie_mod", PATH_LIE)

    threading.Thread(target=stdin_reader, daemon=True).start()

    walk_thread = None
    walk_stop = threading.Event()
    pose_state = "unknown"  # "sit" | "stand" | "lie" | "unknown"

    def start_walk():
        nonlocal walk_thread
        if walk_thread and walk_thread.is_alive():
            print("[INFO] walk already running (type 'stop' first)", flush=True)
            return

        walk_stop.clear()

        def runner():
            try:
                call_best_entry(patrol_mod, dog, walk_stop)
            except Exception as e:
                print(f"[ERROR] walk failed: {e}", flush=True)
                traceback.print_exc()

        walk_thread = threading.Thread(target=runner, daemon=True)
        walk_thread.start()
        print("[OK] walk started (type 'stop' to stop)", flush=True)

    def stop_walk():
        walk_stop.set()
        try:
            dog.body_stop()
        except Exception:
            pass

    # Start pose: stand (slow)
    try:
        safe_stand(dog, from_sit=False)
        pose_state = "stand"
    except Exception:
        pass

    try:
        while not stop_event.is_set():
            try:
                raw = cmd_q.get(timeout=0.1)
            except queue.Empty:
                continue

            cmd = raw.strip().lower()

            if cmd in ("quit", "exit", "q"):
                break

            if cmd == "sit":
                stop_walk()
                safe_sit(dog)
                pose_state = "sit"
                continue

            if cmd == "stand":
                stop_walk()
                safe_stand(dog, from_sit=(pose_state == "sit"))
                pose_state = "stand"
                continue

            if cmd in ("lie", "hinlegen", "lie down"):
                stop_walk()
                try:
                    safe_lie_down(dog)
                    print("[OK] lie down", flush=True)
                    pose_state = "lie"
                except Exception:
                    try:
                        call_best_entry(lie_mod, dog)
                        print("[OK] lie down", flush=True)
                        pose_state = "lie"
                    except Exception as e:
                        print(f"[ERROR] lie failed: {e}", flush=True)
                        traceback.print_exc()
                continue

            if cmd == "paw":
                stop_walk()
                try:
                    call_best_entry(paw_mod, dog)
                    print("[OK] paw done", flush=True)
                    pose_state = "sit"
                except Exception as e:
                    print(f"[ERROR] paw failed: {e}", flush=True)
                    traceback.print_exc()
                continue

            if cmd == "walk":
                start_walk()
                continue

            if cmd == "stop":
                stop_walk()
                print("[OK] stopped", flush=True)
                continue

            print("[ERR] Unknown command. Type a command from the list above.", flush=True)

    except KeyboardInterrupt:
        pass
    finally:
        print("[INFO] shutting down...", flush=True)
        stop_event.set()
        stop_walk()

        try:
            safe_stand(dog, from_sit=False)
        except Exception:
            pass

        try:
            dog.close()
        except Exception:
            pass

        print("[INFO] Bye.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[FATAL] Unhandled exception: {e}", flush=True)
        traceback.print_exc()
        time.sleep(0.2)
        raise
