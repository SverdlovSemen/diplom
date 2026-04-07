from __future__ import annotations

import logging
import sys


def configure_logging(level: str) -> None:
    # Базовая конфигурация логирования (stdout), пригодная для Docker.
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

