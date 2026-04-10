from stockagent.universe.base import UniverseLoader


class StaticUniverseLoader(UniverseLoader):
    def __init__(self, symbols: list[str]) -> None:
        self.symbols = symbols

    def load_symbols(self, limit: int | None = None) -> list[str]:
        if limit is None:
            return list(self.symbols)
        return list(self.symbols[:limit])
