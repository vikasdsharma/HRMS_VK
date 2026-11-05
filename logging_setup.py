"""Rotating log files in HRMS_DATA_DIR/logs:  app.log (everything), error.log (warnings+), access.log (requests)."""
import logging
import os
from logging.handlers import RotatingFileHandler

import config

_done = False


def setup():
    global _done
    if _done:
        return
    _done = True
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    root = logging.getLogger("hrms")
    root.setLevel(config.LOG_LEVEL)
    root.propagate = False

    def add(handler, level=None):
        handler.setFormatter(fmt)
        if level:
            handler.setLevel(level)
        root.addHandler(handler)

    add(logging.StreamHandler())
    add(RotatingFileHandler(os.path.join(config.LOG_DIR, "app.log"), maxBytes=5_000_000, backupCount=5,
                            encoding="utf-8"))
    add(RotatingFileHandler(os.path.join(config.LOG_DIR, "error.log"), maxBytes=5_000_000, backupCount=5,
                            encoding="utf-8"), logging.WARNING)
    acc = logging.getLogger("hrms.access")
    acc.propagate = False
    h = RotatingFileHandler(os.path.join(config.LOG_DIR, "access.log"), maxBytes=5_000_000, backupCount=5,
                            encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    acc.addHandler(h)
    acc.setLevel(logging.INFO)
