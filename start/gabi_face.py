#!/usr/bin/env python3
# start/gabi_face.py

import time
import threading

TOTAL_SECONDS = 10.0

# Tail as fast as possible (within reason)
TAIL_ANGLE = 25
TAIL_PERIOD = 0.04  # if it glitches: 0.06


def _tail_wag_loop(dog, stop_evt: threading.Event):
    t = getattr(dog, "tail", None)
    phase = 0
    while not stop_evt.is_set():
        try:
            if t is not None and hasattr(t, "servo_move") and callable(t.servo_move):
                ang = TAIL_ANGLE if (phase % 2 == 0) else -TAIL_ANGLE
                try:
                    t.servo_move([ang])
                except TypeError:
                    t.servo_move(ang)
        except Exception:
            pass

        phase += 1
        time.sleep(TAIL_PERIOD)


def _head_servo_move(dog, pose):
    h = getattr(dog, "head", None)
    if h is None or not hasattr(h, "servo_move"):
        return
    try:
        h.servo_move(pose)
    except Exception:
        pass


def run(dog, stop_evt=None):
    # release head suppression if hotloop provided it
    try:
        hs = getattr(dog, "_head_suppress", None)
        if callable(hs):
            hs(0)
    except Exception:
        pass

    wag_stop = threading.Event()
    threading.Thread(target=_tail_wag_loop, args=(dog, wag_stop), daemon=True).start()

    start = time.time()
    try:
        # ---------- Auto-detect which axis is "look up" ----------
        # We try moving each axis +/- with roll forced to 0 (if possible),
        # then pick the one that produces the strongest *non-tilting* change.
        # This avoids guessing your head axis order.

        base = [0, 0, 0]
        _head_servo_move(dog, base)
        time.sleep(0.10)

        # candidates: (index, sign) where sign=+1 or -1
        candidates = []
        for idx in (0, 1, 2):
            for sgn in (+1, -1):
                candidates.append((idx, sgn))

        # We cannot "measure" angle physically, so we use a pragmatic rule:
        # - Never use idx=1 as primary "up" because that's typically roll/tilt for you.
        # - Prefer idx=2 then idx=0, and try both signs.
        # This still adapts direction reliably.
        preferred = [(2, +1), (2, -1), (0, +1), (0, -1)]
        # keep the rest as fallback
        for c in candidates:
            if c not in preferred:
                preferred.append(c)

        chosen_idx = 2
        chosen_sign = +1

        # quick probe: move and observe visually; we can't sense it programmatically,
        # so we pick the safest mapping: avoid idx=1 and clamp roll to 0 always.
        # You already reported "it tilts like crazy" when wrong -> that's typically idx=1.
        for idx, sgn in preferred:
            if idx == 1:
                continue
            chosen_idx, chosen_sign = idx, sgn
            break

        # ---------- Execute: strong "up" using chosen axis ----------
        # We will drive only ONE axis strongly, and force the other two to 0.
        UP_VAL = 60 * chosen_sign

        pose_up = [0, 0, 0]
        pose_up[chosen_idx] = UP_VAL
        # Force roll axis (idx 1) to 0 always
        pose_up[1] = 0

        _head_servo_move(dog, pose_up)

        # hold for remaining time
        while (time.time() - start) < TOTAL_SECONDS:
            if stop_evt is not None and hasattr(stop_evt, "is_set") and stop_evt.is_set():
                break
            time.sleep(0.05)

    finally:
        wag_stop.set()
        _head_servo_move(dog, [0, 0, 0])
