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


def safe_stand(dog: Pidog, from_sit: bool):
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
    dog.do_action("sit", speed=SIT_SPEED_1)
    dog.wait_all_done()
    time.sleep(SIT_PAUSE)

    dog.do_action("sit", speed=SIT_SPEED_2)
    dog.wait_all_done()


def safe_lie_down(dog: Pidog):
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
        # Stop-Event setzen + sofort Hardware stoppen
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
