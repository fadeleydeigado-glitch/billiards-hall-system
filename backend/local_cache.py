"""进程内 TTL 缓存。

设计边界：
- 只缓存读多写少、允许短暂旧值的数据；
- 使用单调时钟处理 TTL，避免系统时钟回拨；
- 每个 key 维护独立版本号，防止慢查询把失效前的旧值重新回填；
- 所有公开操作都必须降级，不允许缓存故障影响主业务。

注意：这是单进程缓存。多个 Uvicorn/gunicorn worker 之间不共享内存，
多进程部署时必须禁用本地缓存或替换为 Redis 等外部缓存。
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from typing import Any, Callable


logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 300

PRODUCTS_LIST_KEY = "products:list"
TABLES_CONFIG_KEY = "tables:config"


class LocalCache:
    """线程安全的进程内缓存，值使用深拷贝隔离。"""

    def __init__(
        self,
        default_ttl: int = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._entries: dict[str, tuple[float, Any]] = {}
        self._versions: dict[str, int] = {}
        self._default_ttl = default_ttl
        self._clock = clock
        self._lock = threading.Lock()

    def get_with_version(self, key: str) -> tuple[Any | None, int]:
        """返回缓存值副本和当前版本号；未命中或过期时值为 None。

        调用方必须先保存 version，再查询数据库。数据库查询完成后，
        使用该 version 调用 set_if_version。
        """
        try:
            with self._lock:
                version = self._versions.get(key, 0)
                entry = self._entries.get(key)
                if entry is None:
                    return None, version

                expire_at, value = entry
                if self._clock() >= expire_at:
                    self._entries.pop(key, None)
                    return None, version

                return copy.deepcopy(value), version
        except Exception as error:
            logger.warning(
                "本地缓存读取失败，已降级为查数据库：key=%s，error=%s",
                key,
                error,
            )
            return None, 0

    def set_if_version(
        self,
        key: str,
        value: Any,
        expected_version: int,
        ttl: int = DEFAULT_TTL_SECONDS,
    ) -> bool:
        """版本未变化时回填缓存；版本已变化则丢弃本次旧数据。"""
        try:
            with self._lock:
                current_version = self._versions.get(key, 0)
                if current_version != expected_version:
                    return False

                if not isinstance(ttl, (int, float)) or ttl <= 0:
                    return False

                cached_value = copy.deepcopy(value)
                expire_at = self._clock() + ttl
                self._entries[key] = (expire_at, cached_value)
                return True
        except Exception as error:
            logger.warning(
                "本地缓存回填失败，本次结果不缓存：key=%s，error=%s",
                key,
                error,
            )
            return False

    def invalidate(self, key: str) -> bool:
        """删除 key 并递增版本号，使并发中的旧查询无法回填。"""
        try:
            with self._lock:
                # 先递增版本号，再删值；即使后续删除异常，版本闸门仍然有效。
                self._versions[key] = self._versions.get(key, 0) + 1
                self._entries.pop(key, None)
                return True
        except Exception as error:
            logger.warning(
                "本地缓存失效失败，主业务继续执行：key=%s，error=%s",
                key,
                error,
            )
            return False

    def clear(self) -> bool:
        """清空缓存和版本号；用于测试或进程内显式重置。"""
        try:
            with self._lock:
                self._entries.clear()
                self._versions.clear()
                return True
        except Exception as error:
            logger.warning("本地缓存清空失败：error=%s", error)
            return False


_default_cache = LocalCache()


def get_with_version(key: str) -> tuple[Any | None, int]:
    return _default_cache.get_with_version(key)


def set_if_version(
    key: str,
    value: Any,
    expected_version: int,
    ttl: int = DEFAULT_TTL_SECONDS,
) -> bool:
    return _default_cache.set_if_version(
        key,
        value,
        expected_version,
        ttl=ttl,
    )


def invalidate(key: str) -> bool:
    return _default_cache.invalidate(key)


def clear_all() -> bool:
    return _default_cache.clear()
