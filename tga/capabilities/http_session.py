"""Secret-free lifecycle manager for per-Solver HTTP cookie sessions."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from http.cookiejar import CookieJar
from urllib.parse import urlparse


def origin_of(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    if not parsed.scheme or not host:
        raise ValueError("invalid HTTP origin")
    default = (parsed.scheme == "http" and parsed.port in {None, 80}) or (parsed.scheme == "https" and parsed.port in {None, 443})
    return f"{parsed.scheme}://{host}" if default else f"{parsed.scheme}://{host}:{parsed.port}"


@dataclass
class _Profile:
    jar: CookieJar = field(default_factory=CookieJar)
    request_count: int = 0
    generation: int = 1


class HTTPSessionRegistry:
    """Keep CookieJar values only in memory and isolate every origin."""

    def __init__(self) -> None:
        self._profiles: dict[tuple[str, str, str], _Profile] = {}
        self._rebuilds: dict[tuple[str, str], int] = {}
        self._lock = threading.RLock()

    def acquire(self, *, task_id: str, solver_id: str, url: str, persistent: bool = True) -> tuple[CookieJar, dict]:
        origin = origin_of(url)
        key = (task_id, solver_id, origin)
        with self._lock:
            reused = persistent and key in self._profiles
            if persistent:
                profile = self._profiles.setdefault(key, _Profile())
            else:
                profile = _Profile()
            profile.request_count += 1
            origin_count = len({item[2] for item in self._profiles if item[:2] == key[:2]})
            return profile.jar, {
                "profile": "persistent" if persistent else "stateless",
                "origin": origin,
                "reused": reused,
                "request_count": profile.request_count,
                "origin_count": origin_count,
                "generation": profile.generation,
                "rebuild_count": self._rebuilds.get(key[:2], 0),
                "cross_process_recovery": False,
            }

    def destroy(self, *, task_id: str, solver_id: str | None = None) -> int:
        with self._lock:
            keys = [
                key for key in self._profiles
                if key[0] == task_id and (solver_id is None or key[1] == solver_id)
            ]
            for key in keys:
                self._profiles[key].jar.clear()
                del self._profiles[key]
            subjects = {(key[0], key[1]) for key in keys}
            for subject in subjects:
                self._rebuilds[subject] = self._rebuilds.get(subject, 0) + 1
            return len(keys)

    def snapshot(self, *, task_id: str, solver_id: str) -> dict:
        with self._lock:
            profiles = [profile for key, profile in self._profiles.items() if key[:2] == (task_id, solver_id)]
            return {
                "profile": "persistent",
                "active": bool(profiles),
                "origin_count": len(profiles),
                "request_count": sum(item.request_count for item in profiles),
                "rebuild_count": self._rebuilds.get((task_id, solver_id), 0),
                "cross_process_recovery": False,
            }
