"""Cron 表达式解析与下次运行时间计算工具。

对应 SubTask 18.2：为定时任务调度系统提供 cron 解析与时区感知的下一次运行时间计算。

为什么需要专门的 cron 工具？
----------------------------
1. **统一入口**：API 创建/编辑定时任务、调度器派发后更新 next_run_at，都需要
   根据 cron 表达式计算下次执行时间。集中在本模块实现，避免散落在多处导致
   时区处理不一致。
2. **时区正确性**：用户配置的 cron 表达式是"本地时间语义"（如
   ``0 9 * * 1-5`` 表示工作日早上 9 点），而数据库中 next_run_at 必须以 UTC
   存储（便于全局排序与跨时区比较）。本模块负责"本地 cron → UTC 时间"的转换。
3. **格式校验**：在 API 层提前校验 cron 表达式格式，避免无效表达式写入数据库
   后在调度器中抛异常。

Cron 5 段格式说明
-----------------
标准 cron 表达式由 5 段组成，从左到右依次为：

    ┌──────── 分钟 (0-59)
    │ ┌────── 小时 (0-23)
    │ │ ┌──── 日 (1-31)
    │ │ │ ┌── 月 (1-12 或 JAN-DEC)
    │ │ │ │ ┌ 周 (0-6 或 SUN-SAT，0/7 均表示周日)
    │ │ │ │ │
    * * * * *

支持的语法：
- ``*``：任意值（如分钟段的 ``*`` 表示每分钟）
- ``,n``：列表（如 ``1,15`` 在分钟段表示每小时的第 1 和第 15 分钟）
- ``-``：范围（如 ``9-17`` 在小时段表示 9 点到 17 点）
- ``/``：步长（如 ``*/15`` 在分钟段表示每 15 分钟；``10-20/2`` 表示 10 到 20 之间每 2 个单位）

时区处理逻辑
------------
1. 输入的 ``from_time`` 默认为当前 UTC 时间（``datetime.now(timezone.utc)``）。
2. 将 ``from_time`` 转换为 ``timezone`` 指定的本地时间（如 ``Asia/Shanghai``），
   这样 croniter 按"本地时间语义"计算下次运行点。
3. croniter 计算出的下次运行时间仍是带时区的本地时间，直接转换为 UTC 返回。
4. 最终返回的 UTC 时间会写入 ``schedules.next_run_at``，调度器（Celery Beat）
   按 UTC 全局扫描到期任务。

为什么不在数据库层做时区转换？
    数据库中所有时间字段统一用 UTC 存储，避免多时区部署时的比较混乱。
    时区只在"用户语义 → UTC"的转换边界处理一次（即本模块），其余代码全用 UTC。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from croniter import croniter
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if TYPE_CHECKING:
    pass


def parse_cron(expression: str) -> bool:
    """校验 cron 表达式格式是否合法（5 段：分 时 日 月 周）。

    校验规则
    --------
    1. 必须为非空字符串。
    2. 按空白拆分后必须恰好 5 段（标准 cron 格式，不支持秒与年）。
    3. 交给 croniter 做语法校验：croniter 会解析每一段的范围、列表、范围、
       步长语法，非法表达式会抛 ``ValueError``。

    Args:
        expression: 待校验的 cron 表达式，如 ``"0 9 * * 1-5"``。

    Returns:
        True 表示表达式合法；False 表示非法（格式错误或字段越界）。

    Example:
        >>> parse_cron("0 9 * * 1-5")
        True
        >>> parse_cron("invalid")
        False
        >>> parse_cron("60 9 * * *")  # 分钟越界
        False
    """
    # 空值保护：None 或空字符串直接判定非法
    if not expression or not expression.strip():
        return False

    # 按任意空白拆分（兼容多空格/Tab），标准 cron 恰好 5 段
    parts = expression.strip().split()
    if len(parts) != 5:
        # 非 5 段：不支持秒（6 段）与年（7 段）扩展语法
        return False

    try:
        # croniter.is_valid 不需要基准时间，仅做语法校验
        # 返回 True/False，不抛异常
        return croniter.is_valid(expression)
    except (ValueError, KeyError):
        # 极少数情况下 croniter 可能抛 ValueError/KeyError（如未知别名）
        # 统一视为非法表达式
        return False


def compute_next_run(
    cron_expression: str,
    timezone_str: str,
    from_time: datetime | None = None,
) -> datetime:
    """根据 cron 表达式与时区计算下次运行时间（返回 UTC datetime）。

    时区转换流程
    ------------
    1. ``from_time`` 默认为当前 UTC 时间（``datetime.now(timezone.utc)``）。
       调度器在派发任务后会以"当前时间"为基准计算下次运行点。
    2. 将 ``from_time`` 转换为 ``timezone_str`` 指定的本地时间：
       - 例：``from_time = 2026-07-30T01:30:00Z``，``timezone_str = "Asia/Shanghai"``
       - 本地时间 = 2026-07-30T09:30:00+08:00
    3. croniter 以"本地时间语义"计算下一次 cron 触发点：
       - 例：``cron_expression = "0 10 * * *"``（每天 10:00 本地时间）
       - 下次触发 = 2026-07-30T10:00:00+08:00
    4. 将本地触发时间转换为 UTC 返回：
       - 返回 = 2026-07-30T02:00:00Z
    5. 该 UTC 时间写入 ``schedules.next_run_at``，调度器按 UTC 扫描。

    为什么要把 from_time 先转本地再算？
        cron 表达式是用户本地时间语义（如"工作日早上 9 点"），
        若直接用 UTC 计算，会导致夏令时切换或跨时区部署时触发点错乱。
        先把基准时间转本地，croniter 就能按本地语义正确推算下一次触发点。

    Args:
        cron_expression: 合法的 cron 表达式（5 段）。
            调用前应先用 ``parse_cron`` 校验，本函数不再重复校验。
        timezone_str: IANA 时区名称，如 ``"Asia/Shanghai"`` / ``"UTC"`` / ``"America/New_York"``。
        from_time: 计算基准时间（含时区信息的 datetime）。
            为 None 时使用当前 UTC 时间。
            传入的 naive datetime（无时区）会被视为 UTC。

    Returns:
        下次运行时间（带 UTC 时区信息的 datetime），写入数据库 next_run_at。

    Raises:
        ValueError: cron 表达式非法，或时区名称无效。

    Example:
        >>> from datetime import datetime, timezone
        >>> base = datetime(2026, 7, 30, 1, 30, tzinfo=timezone.utc)
        >>> compute_next_run("0 10 * * *", "Asia/Shanghai", base)
        datetime.datetime(2026, 7, 30, 2, 0, tzinfo=datetime.timezone.utc)
    """
    # ------------------------------------------------------------------
    # 步骤 1：确定基准时间（默认当前 UTC）
    # ------------------------------------------------------------------
    if from_time is None:
        # 未传基准时间：使用当前 UTC 时间
        from_time = datetime.now(timezone.utc)
    elif from_time.tzinfo is None:
        # 传入 naive datetime：视为 UTC，补充时区信息
        # 避免后续 astimezone 转换时按系统本地时区解释
        from_time = from_time.replace(tzinfo=timezone.utc)

    # ------------------------------------------------------------------
    # 步骤 2：加载目标时区
    # ------------------------------------------------------------------
    # ZoneInfo 是 Python 3.9+ 标准库的 IANA 时区实现，自动处理夏令时
    # 无效时区名（如 "Asia/Beijing"）会抛 ZoneInfoNotFoundError
    try:
        tz = ZoneInfo(timezone_str)
    except ZoneInfoNotFoundError as exc:
        # 转为 ValueError 便于上层统一处理
        raise ValueError(f"无效的时区名称: {timezone_str}") from exc

    # ------------------------------------------------------------------
    # 步骤 3：将基准时间转换为本地时间
    # ------------------------------------------------------------------
    # astimezone 会调整时刻不变，仅切换时区表示
    # 例：2026-07-30T01:30:00Z → 2026-07-30T09:30:00+08:00（上海）
    local_from = from_time.astimezone(tz)

    # ------------------------------------------------------------------
    # 步骤 4：用 croniter 计算下一次触发点（本地时间语义）
    # ------------------------------------------------------------------
    # croniter 接受 cron 表达式与基准时间，get_next 返回下一次触发时间
    # 返回的 datetime 带有与 local_from 相同的时区信息
    if not croniter.is_valid(cron_expression):
        # 表达式非法：抛 ValueError，由 API 层捕获转为 VALIDATION_ERROR
        raise ValueError(f"非法的 cron 表达式: {cron_expression}")

    cron = croniter(cron_expression, local_from)
    # get_next(ret_type=datetime) 返回下一次触发时间（datetime 类型）
    next_local: datetime = cron.get_next(datetime)

    # ------------------------------------------------------------------
    # 步骤 5：转换回 UTC 并返回
    # ------------------------------------------------------------------
    # next_local 已带时区信息（与 tz 一致），astimezone(utc) 转换为 UTC
    # 写入数据库统一用 UTC，避免多时区部署时的比较混乱
    return next_local.astimezone(timezone.utc)
