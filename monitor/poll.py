"""Poll the Environment Agency for new readings.

    python3 -m monitor.poll           # poll every 5 minutes
    python3 -m monitor.poll --once    # one cycle, then exit

serve.py runs the same loop in a background thread, so a deployment needs
only the one process.
"""

import argparse
import threading
import time

from . import db, ea, live

# Stations report every 15 minutes but not in step, and the EA publishes each
# reading some minutes after it is taken, so polling more often than the
# reporting interval shortens the wait for each new reading.
INTERVAL = 5 * 60
STATION_REFRESH = 24 * 3600


def cycle():
    con = db.connect()
    try:
        refreshed = int(db.get_meta(con, "stations_refreshed", 0))
        if time.time() - refreshed > STATION_REFRESH:
            n = ea.refresh_stations(con)
            db.set_meta(con, "stations_refreshed", int(time.time()))
            con.commit()
            ea.log(f"station list refreshed: {n} stations")
        # The latest readings go to the map first; filling gaps can take a
        # while when several stations need their history fetched.
        latest, held = ea.store_latest(con)
        db.set_meta(con, "last_poll", int(time.time()))
        con.commit()
        live.write(con)
        filled = ea.fill_gaps(con, latest, held)
        if filled:
            live.write(con)
        ea.log(f"poll: {len(latest)} latest readings, {filled} series filled in")
    finally:
        con.close()


def run_forever(stop=None):
    stop = stop or threading.Event()
    while not stop.is_set():
        started = time.time()
        try:
            cycle()
        except Exception as e:  # noqa: BLE001 - log, wait, try again next interval
            ea.log(f"poll failed: {e}")
        stop.wait(max(60, INTERVAL - (time.time() - started)))


def start_thread():
    stop = threading.Event()
    threading.Thread(target=run_forever, args=(stop,), name="poller", daemon=True).start()
    return stop


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true", help="run one cycle and exit")
    args = ap.parse_args()
    if args.once:
        cycle()
    else:
        run_forever()


if __name__ == "__main__":
    main()
