"""验证对象存储孤儿清理只影响过期且无数据库引用的应用文件。"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from knowledge_core.infrastructure.storage import LocalObjectStorage


def test_cleanup_orphans_preserves_referenced_and_recent_objects(tmp_path) -> None:
    """延迟清理不得删除仍被引用或仍在观察窗口内的对象。"""
    storage = LocalObjectStorage(tmp_path)
    referenced_key = "app/a/env/e/doc/d/referenced.txt"
    recent_key = "app/a/env/e/doc/d/recent.txt"
    orphan_key = "app/a/env/e/doc/d/orphan.txt"
    for key in (referenced_key, recent_key, orphan_key):
        path = storage._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key, encoding="utf-8")
    old_timestamp = (datetime.now(UTC) - timedelta(hours=48)).timestamp()
    os.utime(storage._resolve(referenced_key), (old_timestamp, old_timestamp))
    os.utime(storage._resolve(orphan_key), (old_timestamp, old_timestamp))

    deleted = storage.cleanup_orphans(
        {referenced_key}, before=datetime.now(UTC) - timedelta(hours=24)
    )

    assert deleted == [orphan_key]
    assert storage._resolve(referenced_key).is_file()
    assert storage._resolve(recent_key).is_file()
    assert not storage._resolve(orphan_key).exists()
