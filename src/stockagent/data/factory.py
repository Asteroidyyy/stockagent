from stockagent.config import get_settings
from stockagent.data.akshare_provider import AkshareMarketDataProvider
from stockagent.data.base import MarketDataProvider
from stockagent.data.mock import MockMarketDataProvider
from stockagent.data.tushare_provider import TushareMarketDataProvider


def build_market_data_provider() -> MarketDataProvider:
    settings = get_settings()

    if settings.data_provider == "akshare":
        return AkshareMarketDataProvider()
    if settings.data_provider == "tushare":
        return TushareMarketDataProvider()

    return MockMarketDataProvider()
