from stockagent.config import get_settings
from stockagent.universe.akshare import AkshareCsi500UniverseLoader
from stockagent.universe.base import UniverseLoader
from stockagent.universe.static import StaticUniverseLoader


def build_universe_loader() -> UniverseLoader:
    settings = get_settings()

    if settings.universe_name == "csi500":
        return AkshareCsi500UniverseLoader()

    return StaticUniverseLoader(symbols=[])
