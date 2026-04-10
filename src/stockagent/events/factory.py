from stockagent.config import get_settings
from stockagent.events.akshare_provider import AkshareEventProvider
from stockagent.events.base import EventProvider
from stockagent.events.mock import MockEventProvider


def build_event_provider() -> EventProvider:
    settings = get_settings()

    if settings.event_provider == "akshare":
        return AkshareEventProvider(lookback_days=settings.event_lookback_days)

    return MockEventProvider()
