from __future__ import annotations

import json
from pathlib import Path

from stockagent.config import get_settings, resolve_path
from stockagent.schemas import PositionInput
from stockagent.universe.factory import build_universe_loader


def load_positions() -> list[PositionInput]:
    settings = get_settings()
    payload = _load_json(resolve_path(settings.portfolio_file))
    positions = payload.get("positions", [])
    return [PositionInput.model_validate(item) for item in positions]


def load_candidate_symbols() -> list[str]:
    settings = get_settings()
    payload = _load_json(resolve_path(settings.candidate_file))
    return [str(symbol).strip().upper() for symbol in payload.get("candidate_symbols", []) if symbol]


def load_analysis_candidate_symbols(*, include_default_universe: bool = True) -> list[str]:
    settings = get_settings()
    candidate_symbols = load_candidate_symbols()
    if include_default_universe and settings.universe_name:
        try:
            candidate_symbols.extend(
                build_universe_loader().load_symbols(limit=settings.universe_limit)
            )
        except Exception:
            pass
    return list(dict.fromkeys(candidate_symbols))


def save_candidate_symbols(candidate_symbols: list[str]) -> None:
    settings = get_settings()
    path = resolve_path(settings.candidate_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = [str(symbol).strip().upper() for symbol in candidate_symbols if symbol]
    payload = {"candidate_symbols": list(dict.fromkeys(normalized))}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
