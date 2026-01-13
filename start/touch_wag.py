#!/usr/bin/env python3
# touch_wag.py

import threading
import time
import os

# Polling
TOUCH_POLL_INTERVAL = 0.02

# Touch debounce (stabiler Touch)
TOUCH_ON_STABLE_S  = 0.06
TOUCH_OFF_STABLE_S = 0.12

# --- Tail geometry (as you want it) ---
# Neutral "down" position. Your working value:
TAIL_CENTER = -20

# ±60 swing around center -> 60° right / 60° left
TAIL_SWING = 40

# Wag speed (smaller interval = faster)
WAG_INTERVAL = 0.05

# Speeds (if library supports it)
WAG_SERVO_SPEED = 200
TAIL_RETURN_SPEED = 150
# -------------------------------------

DEBUG = os.environ.get("TOUCH_DEBUG", "0") == "1"


def _debug_print(*args):
    if DEBUG:
        print("[TOUCH_DEBUG]", *args, flush=True)


def _get_touch_device(dog):
    for name in ("dual_touch", "touch", "head_touch", "cap_touch"):
        dev = getattr(dog, name, None)
        if dev is not None:
            return dev
    return None


def _normalize_touch(raw):
    # Your DualTouch.read() returns strings: N, L, R, LS, RS
    if raw is None:
        return False

    if isinstance(raw, str):
        s = raw.strip().upper()
        if s in ("N", "NONE", "NO", "0", "FALSE", ""):
            return False
        return True

    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return raw != 0
    if isinstance(raw, (list, tuple)):
        return any(_normalize_touch(x) for x in raw)
    if isinstance(raw, dict):
        return any(_normalize_touch(v) for v in raw.values())

    try:
        return bool(raw)
    except Exception:
        return False


def _read_touch(dev):
    if dev is None:
        return False, None

    fn = getattr(dev, "read", None)
    if callable(fn):
        try:
            raw = fn()
            return _normalize_touch(raw), raw
        except Exception:
            pass

    for attr in ("get_value", "get_state", "get_status", "status", "value"):
        obj = getattr(dev, attr, None)

        if not callable(obj) and obj is not None:
            raw = obj
            return _normalize_touch(raw), raw

        if callable(obj):
            try:
                raw = obj()
                return _normalize_touch(raw), raw
            except Exception:
                continue

    return False, None


class TailWagger:
    def __init__(self, dog):
        self.dog = dog
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    def _tail(self):
        return getattr(self.dog, "tail", None)

    def _servo_move(self, tail, pos, speed=None):
        # IMPORTANT: no 0..180 clamp, because your tail accepts negative values (e.g. -40)
        try:
            if speed is None:
                tail.servo_move([pos])
            else:
                tail.servo_move([pos], speed=speed)
        except TypeError:
            tail.servo_move([pos])

    def _set_center_down(self):
        tail = self._tail()
        if tail is None or not hasattr(tail, "servo_move"):
            return
        try:
            self._servo_move(tail, TAIL_CENTER, speed=TAIL_RETURN_SPEED)
        except Exception:
            pass

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        self._set_center_down()

    def _run(self):
        tail = self._tail()
        if tail is None or not hasattr(tail, "servo_move"):
            print("[WARN] Tail servo_move not available -> cannot wag.", flush=True)
            return

        # enforce center
        self._set_center_down()
        time.sleep(0.05)

        left  = TAIL_CENTER - TAIL_SWING
        right = TAIL_CENTER + TAIL_SWING

        # If direction feels inverted, swap order here:
        targets = [right, left]
        i = 0

        while not self._stop.is_set():
            pos = targets[i % 2]
            i += 1
            try:
                self._servo_move(tail, pos, speed=WAG_SERVO_SPEED)
            except Exception:
                pass
            time.sleep(WAG_INTERVAL)

        self._set_center_down()


def run(dog, stop_evt=None):
    dev = _get_touch_device(dog)
    if dev is None:
        print("[WARN] No touch device found on dog.", flush=True)
        return

    print(f"[INFO] Touch watcher active using: {type(dev).__name__}", flush=True)

    wag = TailWagger(dog)

    stable_state = False
    on_since = None
    off_since = None

    raw_touched, raw = _read_touch(dev)
    stable_state = raw_touched
    print(f"[INFO] Touch initial: raw={raw} touched={raw_touched}", flush=True)
    if stable_state:
        wag.start()

    last_print_state = stable_state

    while (stop_evt is None) or (not stop_evt.is_set()):
        raw_touched, raw = _read_touch(dev)
        now = time.time()

        if DEBUG:
            _debug_print("raw =", raw, "| touched =", raw_touched)

        if raw_touched:
            off_since = None
            if on_since is None:
                on_since = now
        else:
            on_since = None
            if off_since is None:
                off_since = now

        if (not stable_state) and raw_touched and on_since is not None and (now - on_since) >= TOUCH_ON_STABLE_S:
            stable_state = True
            wag.start()

        if stable_state and (not raw_touched) and off_since is not None and (now - off_since) >= TOUCH_OFF_STABLE_S:
            stable_state = False
            wag.stop()

        if stable_state != last_print_state:
            print(f"[INFO] Touch {'ON' if stable_state else 'OFF'} (raw={raw})", flush=True)
            last_print_state = stable_state

        time.sleep(TOUCH_POLL_INTERVAL)

    wag.stop()
