#!/usr/bin/env python3
# start/pidog_inneo.py
import os
import sys
import threading
import queue
import importlib.util
import traceback
import time

from pidog import Pidog

# ----------------- CONFIG -----------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PATH_PATROL = os.path.join(BASE_DIR, "3_patrol.py")
PATH_PAW    = os.path.join(BASE_DIR, "angewinkelt.py")
PATH_LIE    = os.path.join(BASE_DIR, "hinlegen.py")
PATH_TOUCH  = os.path.join(BASE_DIR, "touch_wag.py")

PATH_SIT    = os.path.join(BASE_DIR, "sitzen.py")
PATH_STAND  = os.path.join(BASE_DIR, "stehen.py")

# Head anti-jitter (global)
HEAD_DEADBAND = 4
HEAD_MIN_INTERVAL = 0.10

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


# ----------------- Head anti-jitter patch -----------------
_head_lock = threading.Lock()
_head_last_sent_pose = None
_head_last_sent_t = 0.0
_head_pending_pose = None
_head_pending_speed = None
_head_sender_started = False
_head_suppress_until = 0.0


def head_suppress(seconds: float):
    """
    seconds > 0  => extend suppression window
    seconds <= 0 => RELEASE suppression immediately
    """
    global _head_suppress_until
    now = time.time()
    with _head_lock:
        if float(seconds) <= 0.0:
            _head_suppress_until = now
        else:
            _head_suppress_until = max(_head_suppress_until, now + float(seconds))


def _patch_head_servo_move(dog: Pidog):
    global _head_sender_started

    h = getattr(dog, "head", None)
    if h is None or not hasattr(h, "servo_move") or not callable(h.servo_move):
        print("[WARN] head servo_move not available; anti-jitter patch skipped", flush=True)
        return

    orig = h.servo_move

    def _sender():
        global _head_pending_pose, _head_pending_speed, _head_last_sent_pose, _head_last_sent_t
        while not stop_event.is_set():
            now = time.time()
            pose = None
            spd = None

            with _head_lock:
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
            if now < _head_suppress_until:
                return

            if _head_last_sent_pose is not None and len(_head_last_sent_pose) == len(target):
                if all(target[i] == _head_last_sent_pose[i] for i in range(len(target))):
                    return
                if all(abs(target[i] - _head_last_sent_pose[i]) <= HEAD_DEADBAND for i in range(len(target))):
                    return

            _head_pending_pose = target
            _head_pending_speed = speed

    h.servo_move = filtered_servo_move

    if not _head_sender_started:
        _head_sender_started = True
        threading.Thread(target=_sender, daemon=True).start()

    print("[INFO] Head anti-jitter patch enabled (coalesce + deadband + suppression)", flush=True)


def main():
    print("[INFO] pidog_inneo.py starting...", flush=True)
    print(f"[INFO] Python: {sys.version}", flush=True)
    print(f"[INFO] BASE_DIR: {BASE_DIR}", flush=True)

    for p in (PATH_PATROL, PATH_PAW, PATH_LIE, PATH_TOUCH, PATH_SIT, PATH_STAND):
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

    # expose suppression controller to modules
    dog._head_suppress = head_suppress

    _patch_head_servo_move(dog)

    patrol_mod = load_module_from_path("patrol_mod", PATH_PATROL)
    paw_mod    = load_module_from_path("paw_mod", PATH_PAW)
    lie_mod    = load_module_from_path("lie_mod", PATH_LIE)
    touch_mod  = load_module_from_path("touch_mod", PATH_TOUCH)
    sit_mod    = load_module_from_path("sit_mod", PATH_SIT)
    stand_mod  = load_module_from_path("stand_mod", PATH_STAND)

    # touch watcher background
    threading.Thread(target=call_best_entry, args=(touch_mod, dog, stop_event), daemon=True).start()
    print("[INFO] Touch->Tail-wag module started", flush=True)

    threading.Thread(target=stdin_reader, daemon=True).start()

    walk_thread = None
    walk_stop = threading.Event()
    pose_state = "unknown"

    def stop_sit_scan_if_any():
        fn = getattr(sit_mod, "stop_scan", None)
        if callable(fn):
            try:
                fn(dog)
            except Exception:
                pass

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

    # initial stand
    try:
        stand_mod.run(dog, stop_event, from_sit=False)
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
                stop_sit_scan_if_any()
                break

            if cmd == "sit":
                stop_walk()
                try:
                    call_best_entry(sit_mod, dog, stop_event)
                    pose_state = "sit"
                except Exception as e:
                    print(f"[ERROR] sit failed: {e}", flush=True)
                    traceback.print_exc()
                continue

            # Any other command: stop scan and reset head
            stop_sit_scan_if_any()

            if cmd == "stand":
                stop_walk()
                try:
                    stand_mod.run(dog, stop_event, from_sit=(pose_state == "sit"))
                    pose_state = "stand"
                except Exception as e:
                    print(f"[ERROR] stand failed: {e}", flush=True)
                    traceback.print_exc()
                continue

            if cmd in ("lie", "hinlegen", "lie down"):
                stop_walk()
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
        stop_sit_scan_if_any()

        try:
            stand_mod.run(dog, stop_event, from_sit=False)
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
