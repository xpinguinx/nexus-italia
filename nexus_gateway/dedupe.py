from __future__ import annotations

import time
from typing import Dict


class TTLCache:
    def __init__(self, ttl_sec: int) -> None:
        self.ttl_sec = ttl_sec
        self._data: Dict[str, float] = {}

    def add(self, key: str) -> None:
        self._purge()
        self._data[key] = time.time() + self.ttl_sec

    def seen(self, key: str) -> bool:
        self._purge()
        expiry = self._data.get(key)
        return bool(expiry and expiry >= time.time())

    def _purge(self) -> None:
        now = time.time()
        expired = [k for k, exp in self._data.items() if exp < now]
        for k in expired:
            self._data.pop(k, None)
