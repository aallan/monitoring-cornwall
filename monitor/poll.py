"""Poll the Environment Agency for new readings.

    python3 -m monitor.poll           # poll every 15 minutes
    python3 -m monitor.poll --once    # one cycle, then exit

serve.py runs the same loop in a background thread, so a deployment needs
only the one process.
"""

import argparse
import threading
import time

from . import db, ea, live

INTERVAL = 15 * 60
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
        seen, backfilled = ea.poll_latest(con)
        db.set_meta(con, "last_poll", int(time.time()))
        con.commit()
        live.write(con)
        ea.log(f"poll: {seen} latest readings, {backfilled} series backfilled")
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
