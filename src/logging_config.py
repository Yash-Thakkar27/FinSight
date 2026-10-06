"""Logging setup shared by every script: console plus logs/finsight.log."""

import logging

from config.settings import LOG_DIR

LOG_FORMAT = "%(asctime)s %(levelname)s - %(message)s"


def setup_logging(level: int = logging.INFO) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    root = logging.getLogger()
    if root.handlers:  # already configured in this process
        return
    root.setLevel(level)
    formatter = logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
    for handler in (
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "finsight.log", encoding="utf-8"),
    ):
        handler.setFormatter(formatter)
        root.addHandler(handler)
    # yfinance logs its own errors; ours carry the context, so keep its noise down.
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
