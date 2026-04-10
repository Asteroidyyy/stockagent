from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from stockagent.config import get_settings, resolve_path

try:
    from redis import Redis
except ImportError:  # pragma: no cover - optional runtime dependency
    Redis = None


def _build_redis_client() -> Redis | None:
    settings = get_settings()
    if Redis is None or not settings.redis_url:
        return None
    try:
        client = Redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        return client
    except Exception:
        return None


class JsonCache:
    def __init__(self, namespace: str) -> None:
        settings = get_settings()
        self.base_dir = resolve_path(settings.cache_dir) / namespace
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.namespace = namespace
        self.redis_client = _build_redis_client()
        self.redis_prefix = f"stockagent:{namespace}:"

    def _redis_key(self, key: str) -> str:
        return f"{self.redis_prefix}{key}"

    def load(self, key: str) -> Any | None:
        if self.redis_client is not None:
            try:
                cached = self.redis_client.get(self._redis_key(key))
                if cached:
                    return json.loads(cached)
            except Exception:
                pass
        path = self.base_dir / f"{key}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, key: str, payload: Any) -> None:
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        if self.redis_client is not None:
            try:
                self.redis_client.set(self._redis_key(key), serialized)
            except Exception:
                pass
        path = self.base_dir / f"{key}.json"
        path.write_text(serialized, encoding="utf-8")


class TaskStateStore:
    def __init__(self) -> None:
        settings = get_settings()
        self.base_dir = resolve_path(settings.cache_dir) / "tasks"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.redis_client = _build_redis_client()
        self.redis_prefix = "stockagent:tasks:"

    def _redis_key(self, task_id: str) -> str:
        return f"{self.redis_prefix}{task_id}"

    def get(self, task_id: str) -> dict[str, Any] | None:
        if self.redis_client is not None:
            try:
                payload = self.redis_client.get(self._redis_key(task_id))
                if payload:
                    return json.loads(payload)
            except Exception:
                pass

        path = self.base_dir / f"{task_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def set_status(
        self,
        *,
        task_id: str,
        task_type: str,
        status: str,
        detail: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = datetime.utcnow().isoformat()
        existing = self.get(task_id) or {}
        record = {
            "task_id": task_id,
            "task_type": task_type,
            "status": status,
            "created_at": existing.get("created_at", now),
            "updated_at": now,
            "detail": detail,
            "payload": payload or existing.get("payload", {}),
        }
        serialized = json.dumps(record, ensure_ascii=False, indent=2)
        if self.redis_client is not None:
            try:
                self.redis_client.set(self._redis_key(task_id), serialized)
            except Exception:
                pass
        (self.base_dir / f"{task_id}.json").write_text(serialized, encoding="utf-8")
        return record
