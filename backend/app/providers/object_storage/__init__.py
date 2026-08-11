"""对象存储 Provider：支持本地目录或 S3 兼容存储，按 ``projects/{project_id}/`` 前缀隔离。

对应 SubTask 8.1：通过工厂函数 ``get_object_storage`` 根据
``settings.object_storage_provider`` 返回对应实现，业务代码依赖抽象接口，
便于在本地开发与生产 S3 间切换而无需修改业务代码。

支持的 Provider
----------------
- ``local``：本地磁盘存储（默认，开发与单机部署使用）
- ``s3``：S3 兼容对象存储（生产环境，Task 8+ 实现具体类）
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Protocol, runtime_checkable

from app.core.config import settings


@runtime_checkable
class ObjectStorageClient(Protocol):
    """对象存储客户端协议：定义统一接口约束。

    所有 Provider 实现（LocalStorageClient / S3StorageClient）必须满足此协议，
    业务代码通过此协议访问存储，不依赖具体实现类。
    """

    def save(self, key: str, content: bytes) -> str:
        """写入文件，返回 storage_key。"""
        ...

    def read(self, key: str) -> bytes:
        """读取文件字节数据。"""
        ...

    def delete(self, key: str) -> bool:
        """删除文件，返回是否删除成功。"""
        ...


@lru_cache
def get_object_storage() -> Any:
    """获取对象存储客户端单例。

    根据 ``settings.object_storage_provider`` 返回对应实现：
        - ``local``：返回 ``LocalStorageClient``（默认）
        - ``s3``：返回 ``S3StorageClient``（TODO Task 8+ 实现）

    使用 ``lru_cache`` 缓存客户端实例：
        - LocalStorageClient 内部仅持有根目录路径，无连接池，缓存可安全复用
        - S3 客户端包含连接池，缓存避免重复创建开销
        - 测试时可通过 ``get_object_storage.cache_clear()`` 重置

    Returns:
        对象存储客户端实例，满足 ``ObjectStorageClient`` 协议。

    Raises:
        ValueError: 配置的 provider 不被支持。
    """
    provider = settings.object_storage_provider.lower()

    if provider == "local":
        # 本地存储：开发与单机部署默认使用
        from app.providers.object_storage.local_storage import LocalStorageClient

        return LocalStorageClient()

    if provider == "s3":
        # S3 兼容存储：TODO Task 8+ 实现，预留入口避免后续修改业务代码
        raise NotImplementedError("S3 对象存储将在后续任务实现")

    raise ValueError(f"不支持的对象存储 Provider：{provider}")
