#!/usr/bin/env python3
import time

SPEED_ACTION = 60


def run(dog, stop_evt=None):
    """
    Hinlegen / Liegen
    Wird vom Hotloop mit call_best_entry(...) aufgerufen
    """

    # Sicherheit: laufende Bewegung stoppen
    try:
        dog.body_stop()
    except Exception:
        pass

    # Hinlegen
    dog.do_action("lie", speed=SPEED_ACTION)
    dog.wait_all_done()

    # kurze Pause, damit die Pose stabil ist
    time.sleep(0.5)
