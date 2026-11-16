"""Run the poll handler on a timer. Replaces the EventBridge schedule off AWS.

    python scripts/run_poll.py            # every POLLING_INTERVAL_MINUTES (default 15)
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

from config import Config  # noqa: E402
from lambda_function import lambda_handler  # noqa: E402

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

    interval = max(1, Config.POLLING_INTERVAL_MINUTES) * 60
    logger.info("Polling every %s minute(s). Ctrl-C to stop.", interval // 60)
    while not _stop:
        run_once()
        # Sleep in short steps so a stop request is honoured quickly.
        slept = 0
        while slept < interval and not _stop:
            time.sleep(1)
            slept += 1
    logger.info("Stopped.")


if __name__ == "__main__":
    main()
