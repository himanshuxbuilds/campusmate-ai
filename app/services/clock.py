from __future__ import annotations

from datetime import date, datetime

from app import config


def today() -> date:
    """The 'current' date. DEMO_TODAY freezes it so fee/late-fee and 'next exam' answers are reproducible."""
    if config.DEMO_TODAY:
        return datetime.strptime(config.DEMO_TODAY, "%Y-%m-%d").date()
    return date.today()
