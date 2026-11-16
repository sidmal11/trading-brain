"""Run the poll handler on a timer. Replaces the EventBridge schedule off AWS.

    python scripts/run_poll.py            # every POLLING_INTERVAL_MINUTES (default 5)
    python scripts/run_poll.py --once     # one run, then exit (useful from cron)

A failed run is logged and the loop continues, so one bad scan does not stop
alerts for the rest of the day. SIGINT and SIGTERM stop the loop after the
current run finishes.
"""

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from lambda_function import lambda_handler  # noqa: E402
from utils.settings_store import effective_settings  # noqa: E402

logger = logging.getLogger("run_poll")
_stop = False


def _request_stop(signum, frame):
    global _stop
    _stop = True
    logger.info("Stop requested; finishing the current run first.")


def run_once():
    try:
        result = lambda_handler({}, None)
        logger.info("Run finished: %s", result.get("body") if isinstance(result, dict) else result)
    except Exception:
        logger.exception("Poll run failed; will retry on the next interval.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run once and exit.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    if args.once:
        run_once()
        return

    logger.info("Polling every interval set in settings. Ctrl-C to stop.")
    while not _stop:
        run_once()
        # Read the interval after each run, so a change in the dashboard applies
        # without a restart. Sleep in short steps so a stop request is honoured quickly.
        interval = effective_settings()["pollIntervalMinutes"] * 60
        slept = 0
        while slept < interval and not _stop:
            time.sleep(1)
            slept += 1
    logger.info("Stopped.")


if __name__ == "__main__":
    main()
