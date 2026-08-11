"""本地对象存储客户端：将文件写入本地磁盘，按 ``projects/{project_id}/`` 前缀隔离。

对应 SubTask 8.1：作为对象存储 Provider 的本地实现，便于开发与单机部署。
生产环境可替换为 S3 兼容实现，仅需保持相同的 ``save / read / delete`` 接口。

设计要点（重点：项目隔离前缀）
------------------------------
1. 所有上传文件路径都以 ``projects/{project_id}/{kb_id}/{document_id}{ext}`` 形式存储，
   前缀中的 ``project_id`` 实现存储层项目隔离：即使其他项目误传相同 document_id，
   也写入不同物理目录，物理隔离杜绝跨项目文件覆盖。
2. 根目录 ``object_storage_path`` 由 ``settings.object_storage_path`` 配置（默认 ``./storage``），
   客户端启动时自动创建，避免每次写入都判空。
3. ``save`` 使用临时文件 + 原子替换（``os.replace``）写入，
   避免并发写入时出现半截文件。
4. 路径穿越防护：拒绝包含 ``..`` 的 key，防止恶意请求越权访问上级目录。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from app.core.config import settings


class LocalStorageClient:
    """本地磁盘对象存储客户端。

    所有方法的 ``key`` 都为相对路径（如 ``projects/abc/doc.pdf``），
    客户端内部拼接 ``object_storage_path`` 根目录后操作真实文件。

    Attributes:
        root: 存储根目录绝对路径，由 ``settings.object_storage_path`` 决定。
    """

    def __init__(self, root: str | None = None) -> None:
        """初始化本地存储客户端。

        Args:
            root: 存储根目录，未传则使用 ``settings.object_storage_path``。
                目录不存在时自动创建（含父目录）。
        """
        # 未指定根目录时回退到全局配置
        self.root = Path(root or settings.object_storage_path).resolve()
        # 确保根目录存在：开发环境 ./storage 由首次启动创建
        self.root.mkdir(parents=True, exist_ok=True)

    def _full_path(self, key: str) -> Path:
        """将相对 key 拼接为绝对路径，并做路径穿越防护。

        为什么需要路径穿越防护？
            客户端可上传任意文件名（如 ``../../etc/passwd``），若直接拼接根目录，
            可能越权写入或读取上级目录的敏感文件。
            通过 resolve 后校验路径是否仍在 root 内，杜绝此类攻击。

        Args:
            key: 相对路径，如 ``projects/abc-kb/doc.pdf``。

        Returns:
            绝对路径对象。

        Raises:
            ValueError: key 包含 ``..`` 或解析后越出 root 范围。
        """
        # 显式拒绝 ``..`` 段，多重保险（resolve 后的 is_relative_to 也能拦截）
        if ".." in Path(key).parts:
            raise ValueError(f"非法存储路径（包含 ..）：{key}")

        full = (self.root / key).resolve()
        # 校验解析后的绝对路径仍位于 root 内
        if not full.is_relative_to(self.root):
            raise ValueError(f"非法存储路径（越出根目录）：{key}")
        return full

    def save(self, key: str, content: bytes) -> str:
        """将字节数据写入对象存储，返回存储 key。

        写入流程：
            1. 校验 key 路径合法性
            2. 创建父目录（``parents=True``）
            3. 使用临时文件写入后 ``os.replace`` 原子替换，避免半截文件

        为什么用原子替换？
            若进程在写入过程中崩溃，可能留下半截文件导致后续读取出错。
            临时文件 + ``os.replace`` 保证目标文件要么是旧版本，要么是新版本，
            不会出现"半个新版本"。

        Args:
            key: 存储 key，相对路径，如 ``projects/{project_id}/{kb_id}/{doc_id}.pdf``。
            content: 文件字节数据。

        Returns:
            写入成功的 key（与入参相同），便于调用方记录 ``storage_key``。
        """
        full = self._full_path(key)
        # 确保父目录存在（如 projects/abc-kb/）
        full.parent.mkdir(parents=True, exist_ok=True)

        # 临时文件路径：在目标目录下用 ``.<pid>.tmp`` 前缀避免冲突
        tmp_path = full.with_name(f".{full.name}.{os.getpid()}.tmp")
        try:
            # 二进制写入临时文件
            with open(tmp_path, "wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())  # 强制刷盘，避免 OS 缓存丢失数据
            # 原子替换：POSIX 与 Windows 均支持 os.replace 覆盖目标
            os.replace(tmp_path, full)
        finally:
            # 异常时清理临时文件，避免残留
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        return key

    def read(self, key: str) -> bytes:
        """读取对象存储中的文件内容。

        Args:
            key: 存储 key。

        Returns:
            文件字节数据。

        Raises:
            FileNotFoundError: 文件不存在。
        """
        full = self._full_path(key)
        # 二进制读取，由调用方负责解码
        with open(full, "rb") as f:
            return f.read()

    def delete(self, key: str) -> bool:
        """删除对象存储中的文件。

        Args:
            key: 存储 key。

        Returns:
            True 表示删除成功；False 表示文件不存在。
        """
        full = self._full_path(key)
        if not full.exists():
            return False
        try:
            full.unlink()
            return True
        except OSError:
            return False

    def exists(self, key: str) -> bool:
        """判断文件是否存在。

        Args:
            key: 存储 key。

        Returns:
            True 表示存在；False 表示不存在。
        """
        return self._full_path(key).exists()

    def delete_dir(self, prefix: str) -> int:
        """删除指定前缀目录下所有文件（如清理项目下所有文档）。

        用于项目删除时清理 ``projects/{project_id}/`` 下全部文件。

        Args:
            prefix: 目录前缀，如 ``projects/{project_id}/``。

        Returns:
            删除的文件数量。
        """
        full = self._full_path(prefix.rstrip("/"))
        if not full.exists() or not full.is_dir():
            return 0
        count = 0
        for path in full.rglob("*"):
            if path.is_file():
                try:
                    path.unlink()
                    count += 1
                except OSError:
                    pass
        # 清理空目录
        try:
            shutil.rmtree(full, ignore_errors=True)
        except OSError:
            pass
        return count
