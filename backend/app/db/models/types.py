"""自定义 PostgreSQL 类型：CIText。

SQLAlchemy 2.0.36+ 移除了内置 CIText，需通过 UserDefinedType 自定义。
对应 PostgreSQL 原生 citext 扩展，创建列前需先执行 CREATE EXTENSION citext。

用途：
    项目编码 / 知识库编码等字段使用 CIText，实现大小写不敏感比较，
    例如 ``ai-fund`` 与 ``AI-FUND`` 视为同一编码，避免业务侧重复创建。
"""
from __future__ import annotations

from sqlalchemy.types import UserDefinedType


class CIText(UserDefinedType):
    """PostgreSQL citext 类型：大小写不敏感文本。

    使用前确保数据库已安装 citext 扩展：
        ``CREATE EXTENSION IF NOT EXISTS citext;``
    """

    cache_ok = True  # 允许 SQLAlchemy 缓存该类型，提升编译性能

    def get_col_spec(self) -> str:
        """返回 PostgreSQL DDL 中的列类型声明。"""
        return "CITEXT"

    def bind_processor(self, dialect):
        """参数绑定处理器：Python → DB。

        citext 在 DB 侧自动处理大小写，Python 侧无需转换，直接透传。
        """
        return None

    def result_processor(self, dialect, coltype):
        """结果集处理器：DB → Python。同样直接透传。"""
        return None
