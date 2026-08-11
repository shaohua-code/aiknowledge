"""安全模块：API Key 哈希校验、生成与脱敏。

对应 SubTask 5.2：使用 Argon2id 哈希与校验 API Key。

安全设计说明（务必阅读）
------------------------
1. 为什么只存哈希不存明文？
   数据库一旦被拖库（SQL 注入、备份泄露、内鬼），明文 API Key 会立即被攻击者
   持有，可冒充任意项目调用接口。只存哈希后，攻击者即使拿到 key_hash 也无法逆推
   明文（Argon2 是单向函数），最大限度降低泄露影响。明文 Key 仅在创建时通过
   ``generate_api_key`` 返回一次，由调用方自行妥善保存。

2. 为什么选 Argon2id？
   Argon2 是 2015 年 Password Hashing Competition 冠军算法，三个变体：
   - Argon2d：抗 GPU 暴力破解，但对侧信道攻击敏感
   - Argon2i：抗侧信道攻击，但抗 GPU 破解稍弱
   - Argon2id：混合模式（前半 i 后半 d），同时兼顾两类威胁
   API Key 场景下不存在侧信道（服务端校验），但 argon2-cffi 默认推荐 id 变体，
   兼顾通用性与安全性。参数 ``time_cost / memory_cost / parallelism`` 决定计算成本：
   - time_cost=3：迭代 3 轮，提升 CPU 成本
   - memory_cost=65536（64 MiB）：单次哈希占用 64MB 内存，抑制 GPU 并行
   - parallelism=4：4 路并行，匹配现代 CPU 多核

3. 为什么用 secrets 而非 random？
   Python 标准库 ``random`` 使用 Mersenne Twister，是伪随机且可预测，
   绝不可用于安全场景。``secrets`` 基于操作系统 CSPRNG（/dev/urandom），
   输出密码学安全随机数，是生成 API Key 的正确选择。

4. 前缀 ``ikh_live_`` 的作用
   - 标识 Key 来源（ikh = Intelligent Knowledge Hub），便于日志识别
   - 区分环境（如 ikh_test_ / ikh_live_），后续可扩展
   - 在数据库中按 ``key_prefix`` 索引快速定位候选记录，避免全表 argon2 校验
"""
from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

# 全局 PasswordHasher 实例
# 参数说明：
#   time_cost=3       —— 迭代 3 轮，提升 CPU 计算成本（默认 2，这里加强）
#   memory_cost=65536 —— 64 MiB 内存占用，抑制 GPU 并行暴力破解
#   parallelism=4     —— 4 路并行哈希，匹配现代多核 CPU
# 注意：参数一旦定下，校验端必须使用相同实例（argon2 哈希串自带参数，可自动兼容）
_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


def hash_api_key(raw_key: str) -> str:
    """对原始 API Key 进行 Argon2id 哈希。

    用于 ``generate_api_key`` 时落库前哈希，以及手动重置 Key 时。
    返回值已包含 salt 与参数，可直接存入数据库 ``api_keys.key_hash``。

    Args:
        raw_key: 明文 API Key，形如 ``ikh_live_<32位hex>``。

    Returns:
        Argon2 哈希字符串，形如
        ``$argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>``。

    Note:
        Argon2 自带随机 salt，相同 raw_key 多次调用得到不同哈希，
        因此不能通过哈希值相等来判断 Key 是否相同，必须用 ``verify_api_key``。
    """
    # 直接调用 hash()，argon2-cffi 内部自动生成 salt 并编码参数
    return _HASHER.hash(raw_key)


def verify_api_key(raw_key: str, hashed: str) -> bool:
    """校验原始 API Key 与数据库哈希是否匹配。

    在 ``get_project_context`` 中对候选 api_keys 记录逐个调用本函数，
    匹配则得到调用方身份。返回布尔而非抛异常，便于调用方循环短路。

    Args:
        raw_key: 客户端传入的明文 API Key。
        hashed: 数据库 ``api_keys.key_hash`` 字段值。

    Returns:
        True 表示匹配成功；False 表示不匹配或哈希格式非法。

    异常处理说明：
        - VerifyMismatchError：raw_key 与 hashed 不匹配（最常见，返回 False）
        - InvalidHashError：hashed 不是合法 argon2 哈希串（数据损坏或恶意篡改，
          返回 False 而非抛出，避免单条脏数据导致 500）
        其它异常（如 VerificationError 通用基类）不在此捕获，由上层兜底处理。
    """
    try:
        # argon2 校验：匹配返回 True，不匹配抛 VerifyMismatchError
        return _HASHER.verify(hashed, raw_key)
    except VerifyMismatchError:
        # 最常见分支：Key 不正确，记录日志后返回 False
        return False
    except InvalidHashError:
        # 数据库哈希字段被破坏或并非 argon2 哈希，返回 False 跳过此候选
        return False


def generate_api_key(prefix: str = "ikh_live_") -> tuple[str, str, str]:
    """生成新 API Key，返回明文、前缀与哈希。

    流程：
    1. 使用 ``secrets.token_hex(16)`` 生成 32 位十六进制随机串（128 bit 熵）
    2. 拼接前缀得到明文 Key：``{prefix}{hex}``
    3. 从明文中提取 ``key_prefix``（前 12 位），用于数据库索引快速定位
    4. 对明文进行 Argon2id 哈希，落库 ``key_hash``

    Args:
        prefix: Key 前缀，默认 ``ikh_live_``。可传 ``ikh_test_`` 区分测试环境 Key。

    Returns:
        三元组 ``(raw_key, key_prefix, key_hash)``：
        - raw_key：明文 Key，仅此一次返回，调用方必须立即保存（如展示给用户后丢弃）
        - key_prefix：用于数据库识别的前缀（前 12 位），不参与鉴权
        - key_hash：Argon2 哈希，存入 ``api_keys.key_hash`` 字段

    Example:
        >>> raw, prefix, hashed = generate_api_key()
        >>> raw.startswith("ikh_live_")
        True
        >>> len(raw) == len("ikh_live_") + 32
        True
    """
    # 生成 32 位 hex 随机串（128 bit 熵），secrets 基于 CSPRNG，密码学安全
    random_hex = secrets.token_hex(16)
    # 明文 Key：前缀 + 随机串，前缀便于日志识别与数据库索引
    raw_key = f"{prefix}{random_hex}"
    # key_prefix 取明文前 12 位（"ikh_live_" 9 位 + 前 3 位 hex），
    # 用于数据库 WHERE key_prefix = ? 快速定位候选记录，避免全表 argon2 校验
    key_prefix = raw_key[:12]
    # 对明文进行 Argon2id 哈希，存入数据库（仅存哈希，不可逆）
    key_hash = hash_api_key(raw_key)
    return raw_key, key_prefix, key_hash


def mask_api_key(raw_key: str) -> str:
    """脱敏 API Key，仅保留前 8 位 + ``***``，用于日志记录。

    日志中不能出现完整 API Key，否则日志泄露即等同于 Key 泄露。
    保留前 8 位（含前缀 ``ikh_live``）便于运维快速识别 Key 来源，
    隐藏剩余部分防止重放攻击。

    Args:
        raw_key: 明文 API Key。

    Returns:
        脱敏后的字符串，如 ``ikh_live***``。

    Example:
        >>> mask_api_key("ikh_live_abcdef0123456789")
        'ikh_live***'
    """
    # 取前 8 位 + 固定掩码；若 Key 过短（异常情况），仅保留前 4 位再加掩码
    if len(raw_key) < 8:
        return f"{raw_key[:4]}***"
    return f"{raw_key[:8]}***"
