from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


def hash_secret(raw: str) -> str:
    return _hasher.hash(raw)


def verify_secret(raw: str, encoded: str) -> bool:
    try:
        return _hasher.verify(encoded, raw)
    except (VerifyMismatchError, InvalidHashError):
        return False


def generate_api_key(environment_code: str) -> tuple[str, str]:
    marker = "live" if environment_code == "production" else "test"
    raw = f"aik_{marker}_{secrets.token_urlsafe(32)}"
    # 前缀用于快速定位候选记录，不能作为鉴权结果。
    return raw, raw[:20]
