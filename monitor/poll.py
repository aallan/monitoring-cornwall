"""Poll the Environment Agency for new readings.

    python3 -m monitor.poll           # poll every 5 minutes
    python3 -m monitor.poll --once    # one cycle, then exit

serve.py runs the same loop in a background thread, so a deployment needs
only the one process.
"""

import argparse
import threading
import time

from . import bathing, db, ea, floods, live, marine, overflows

# Stations report every 15 minutes but not in step, and the EA publishes each
# reading some minutes after it is taken, so polling more often than the
# reporting interval shortens the wait for each new reading.
INTERVAL = 5 * 60
STATION_REFRESH = 24 * 3600


def cycle():
    con = db.connect()
    try:
        # The feeds are independent; a failure in one doesn't stop the others.
        # Filling gaps in the gauges' history comes last, because on a first
        # run, or after the server has been down, it can take minutes.
        gaps = None
        for name, poll in (("gauge", poll_gauges), ("overflow", poll_overflows),
                           ("flood warning", poll_floods), ("bathing water", poll_bathing),
                           ("offshore station", poll_marine)):
            try:
                result = poll(con)
                if name == "gauge":
                    gaps = result
            except Exception as e:  # noqa: BLE001
                ea.log(f"{name} poll failed: {e}")
        if gaps:
            try:
                fill_gauge_gaps(con, *gaps)
            except Exception as e:  # noqa: BLE001
                ea.log(f"gauge history fill failed: {e}")
    finally:
        con.close()


def poll_gauges(con):
    """The latest reading at every gauge, written to live.json. Returns what
    fill_gauge_gaps needs to fetch any missing history."""
    refreshed = int(db.get_meta(con, "stations_refreshed", 0))
    if time.time() - refreshed > STATION_REFRESH:
        ea.log("refreshing the station list")
        n = ea.refresh_stations(con)
        db.set_meta(con, "stations_refreshed", int(time.time()))
        con.commit()
        ea.log(f"station list refreshed: {n} stations")
    latest, held = ea.store_latest(con)
    db.set_meta(con, "last_poll", int(time.time()))
    con.commit()
    live.write(con)
    ea.log(f"gauges: {len(latest)} latest readings")
    return latest, held


def fill_gauge_gaps(con, latest, held):
    filled = ea.fill_gaps(con, latest, held)
    if filled:
        live.write(con)
        ea.log(f"gauges: {filled} series filled in")


def poll_floods(con):
    refreshed = int(db.get_meta(con, "flood_areas_refreshed", 0))
    if time.time() - refreshed > STATION_REFRESH:
        n = floods.refresh_areas(con)
        db.set_meta(con, "flood_areas_refreshed", int(time.time()))
        con.commit()
        ea.log(f"flood area list refreshed: {n} areas")
    warnings = floods.poll_warnings(con)
    floods.write_summary(warnings)
    ea.log(f"flood warnings: {len(warnings)} in force")


def poll_overflows(con):
    refreshed = int(db.get_meta(con, "overflows_refreshed", 0))
    if time.time() - refreshed > STATION_REFRESH:
        n = overflows.refresh_overflows(con)
        db.set_meta(con, "overflows_refreshed", int(time.time()))
        if not db.get_meta(con, "overflow_log_started"):
            db.set_meta(con, "overflow_log_started", int(time.time()))
        con.commit()
        ea.log(f"overflow list refreshed: {n} overflows")
    n = overflows.poll_overflows(con)
    overflows.write_summary(con)
    ea.log(f"overflows: {n} statuses")


def poll_bathing(con):
    """Bathing waters change at most daily, so they're read once an hour."""
    if time.time() - int(db.get_meta(con, "bathing_refreshed", 0)) > bathing.REFRESH:
        n = bathing.refresh(con)
        db.set_meta(con, "bathing_refreshed", int(time.time()))
        con.commit()
        ea.log(f"bathing waters: {n}")
    bathing.write_summary(con)


def poll_marine(con):
    """The sea stations report every half hour or hour, so they're read every
    half hour, and straight away whenever none are known yet."""
    known = con.execute("SELECT COUNT(*) FROM marine_stations WHERE active = 1").fetchone()[0]
    if not known or time.time() - int(db.get_meta(con, "marine_refreshed", 0)) > marine.REFRESH:
        n, errors = marine.poll(con)
        db.set_meta(con, "marine_refreshed", int(time.time()))
        con.commit()
        ea.log(f"offshore stations: {n} readings" + (f"; failed {', '.join(errors)}" if errors else ""))
    marine.write_summary(con)


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
