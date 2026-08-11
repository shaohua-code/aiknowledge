"""文本切割器：将长文本切分为适合 Embedding 的 chunk。

对应 SubTask 9.3：实现段落策略切割，保证语义完整性与上下文重叠。

为什么需要切割？
----------------
1. **Embedding 模型输入长度限制**：OpenAI ``text-embedding-3-small`` 最大 8191 token，
   超长文本无法一次向量化。
2. **检索精度**：向量检索对小段语义集中的文本效果更好，整篇文档向量化会稀释语义。
3. **RAG 上下文成本**：召回后拼接上下文时，过长的 chunk 会挤占 LLM 上下文窗口，
   目标 500-800 中文字符（默认 600）是精度与成本的平衡点。

切割策略（段落优先）
--------------------
1. **段落边界优先**：先按双换行（``\\n\\n``）分段，保留段落语义完整性，
   避免在句子中间硬切。
2. **短段落合并**：连续短段落累加，直到接近 ``target_length`` 再输出一个 chunk，
   避免 chunk 过短导致检索召回碎片化。
3. **长段落二次切割**：单段超过 ``target_length`` 时，按 ``target_length`` 硬切，
   并在相邻 chunk 间保留 ``overlap`` 字符重叠，保证边界语义连续性
   （如某句子被切到两个 chunk 时，重叠部分让两 chunk 都包含该句子上下文）。

返回结构
--------
每个 chunk 是 dict：
    ``{"content": str, "page_number": int | None, "section": str | None, "token_count": int}``
- ``content``：分块文本
- ``page_number``：页码（PDF 等分页文档使用，本模块不填，由调用方注入）
- ``section``：章节层级（如 "1.2.3"），本模块不填，由调用方注入
- ``token_count``：用 tiktoken 估算的 token 数，用于入库时写入 document_chunks.token_count

token 数估算
------------
优先使用 ``tiktoken``（OpenAI 官方分词器，精确）；
若环境未安装 tiktoken（pyproject 未声明依赖），降级为字符数 ÷ 1.5 估算
（中文 1 字 ≈ 1.5 token，英文 1 字 ≈ 0.25 token，混合场景取折中）。
"""
from __future__ import annotations

import re
from typing import Any

# tiktoken 可选依赖：未安装时降级为字符数估算
# 避免硬依赖，保证环境无 tiktoken 时入库流程仍可运行
try:
    import tiktoken

    # 使用 cl100k_base 编码：覆盖 text-embedding-3-small / gpt-4 等模型
    _ENCODER = tiktoken.get_encoding("cl100k_base")
    _HAS_TIKTOKEN = True
except Exception:  # pragma: no cover - 依赖缺失时的降级路径
    _ENCODER = None
    _HAS_TIKTOKEN = False


def _estimate_tokens(text: str) -> int:
    """估算文本的 token 数。

    优先用 tiktoken 精确计算；未安装时按字符数 ÷ 1.5 估算
    （中文 1 字约 1.5 token，英文 1 字约 0.25 token，混合场景折中）。

    Args:
        text: 待估算的文本。

    Returns:
        估算的 token 数，至少为 1（避免 0 token 写入数据库语义不清）。
    """
    if not text:
        return 0
    if _HAS_TIKTOKEN and _ENCODER is not None:
        # tiktoken 精确编码后取长度
        return len(_ENCODER.encode(text))
    # 降级估算：字符数 ÷ 1.5，向上取整，至少 1
    return max(1, round(len(text) / 1.5))


def _split_long_paragraph(
    paragraph: str,
    target_length: int,
    overlap: int,
) -> list[str]:
    """对超长段落按 ``target_length`` 硬切，相邻片段保留 ``overlap`` 重叠。

    为什么需要 overlap？
        句子可能在切割边界被截断（如 "项目A的收益率\n达到 20%"），
        overlap 让相邻两个 chunk 都包含边界附近的内容，检索时即使命中
        后一个 chunk，也能拿到前一段的上下文，提升 RAG 答案质量。

    Args:
        paragraph: 待切割的超长段落文本。
        target_length: 每个片段的目标长度（字符数）。
        overlap: 相邻片段的重叠长度（字符数）。

    Returns:
        切割后的片段列表，每个片段长度 ≤ ``target_length``。
    """
    # 步长 = target_length - overlap，保证相邻片段有 overlap 字符重叠
    step = max(1, target_length - overlap)
    chunks: list[str] = []
    start = 0
    total = len(paragraph)

    while start < total:
        # 截取 [start, start + target_length) 区间
        end = start + target_length
        piece = paragraph[start:end]
        chunks.append(piece)
        # 移动步长，保留 overlap 字符重叠
        start += step
        # 若剩余内容不足以形成一个有意义的 chunk（小于 overlap），停止切割
        if total - start < overlap:
            break

    return chunks


def chunk_by_paragraph(
    text: str,
    target_length: int = 600,
    overlap: int = 100,
) -> list[dict[str, Any]]:
    """段落策略切割：先按双换行分段，短段合并、长段二次切割。

    切割流程
    --------
    1. 按双换行 ``\\n\\n`` 拆分为段落列表（保留段落语义边界）。
    2. 遍历段落，维护当前 chunk 缓冲区 ``buffer``：
       - 若 ``buffer + 段落`` 未超过 ``target_length``，并入 buffer；
       - 若超过，先输出 buffer 为一个 chunk，再处理当前段落：
         - 段落自身超长 → 二次切割为多个片段，除最后一片外都直接输出，
           最后一片并入新 buffer（与后续段落合并）；
         - 段落未超长 → 作为新 buffer 起点。
    3. 遍历结束，剩余 buffer 作为最后一个 chunk 输出。

    Args:
        text: 待切割的文本，应已清洗（统一换行、去除控制字符）。
        target_length: 目标 chunk 长度（中文字符数），默认 600。
            建议范围 500-800，过短导致召回碎片化，过长稀释语义。
        overlap: 长段落二次切割时的重叠长度，默认 100。
            建议范围 80-120，约为 target_length 的 15%。

    Returns:
        chunk 字典列表，每项含 ``content`` / ``page_number`` / ``section`` / ``token_count``。
        ``page_number`` 与 ``section`` 本模块固定为 ``None``，由调用方
        （ingestion 任务）根据文档结构注入。

    Example:
        >>> chunks = chunk_by_paragraph("第一段。\\n\\n第二段。", target_length=600)
        >>> len(chunks)
        1
    """
    # 边界处理：空文本或纯空白返回空列表，避免写入空 chunk
    if not text or not text.strip():
        return []

    # 按双换行分段：\\n\\n 是段落的标准分隔符
    # 保留单换行（段内换行），不破坏段内句子结构
    paragraphs = re.split(r"\n\s*\n", text)
    # 过滤空白段落（连续换行可能产生空段）
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    if not paragraphs:
        return []

    chunks: list[str] = []
    buffer = ""  # 当前正在累积的 chunk 文本

    for paragraph in paragraphs:
        # 段落自身超长：直接硬切，避免与 buffer 合并后无法处理
        if len(paragraph) > target_length:
            # 先输出已累积的 buffer（避免长段落内容混入上一个 chunk）
            if buffer:
                chunks.append(buffer)
                buffer = ""

            # 长段落二次切割
            pieces = _split_long_paragraph(paragraph, target_length, overlap)
            # 除最后一片外都直接作为独立 chunk 输出
            for piece in pieces[:-1]:
                chunks.append(piece)
            # 最后一片并入 buffer，与后续段落合并（可能凑成完整 chunk）
            if pieces:
                buffer = pieces[-1]
            continue

        # 短段落：判断能否并入当前 buffer
        # 拼接后的长度（含分隔符 \\n\\n）
        separator = "\n\n" if buffer else ""
        combined_len = len(buffer) + len(separator) + len(paragraph)

        if combined_len <= target_length:
            # 未超目标长度，并入 buffer
            buffer = f"{buffer}{separator}{paragraph}"
        else:
            # 超目标长度，输出当前 buffer，开启新 buffer
            if buffer:
                chunks.append(buffer)
            buffer = paragraph

    # 输出最后一个 buffer（遍历结束后残留）
    if buffer:
        chunks.append(buffer)

    # 转换为 chunk dict 结构，估算 token_count
    # page_number / section 固定 None，由调用方注入
    return [
        {
            "content": chunk_text,
            "page_number": None,
            "section": None,
            "token_count": _estimate_tokens(chunk_text),
        }
        for chunk_text in chunks
        # 过滤空 chunk（防御性，理论上不会出现）
        if chunk_text.strip()
    ]


def chunk_text(
    text: str,
    target_length: int = 600,
    overlap: int = 100,
) -> list[dict[str, Any]]:
    """文本切割入口函数：当前实现走段落策略。

    本函数是 ``chunk_by_paragraph`` 的别名，保留独立函数便于后续扩展
    其他切割策略（如按标题层级、按句子滑动窗口）时通过参数分发。

    Args:
        text: 待切割的文本。
        target_length: 目标 chunk 长度，默认 600。
        overlap: 长段落二次切割的重叠长度，默认 100。

    Returns:
        chunk 字典列表，结构同 ``chunk_by_paragraph``。
    """
    # 当前默认段落策略，后续可按 strategy 参数分发到不同实现
    return chunk_by_paragraph(text, target_length=target_length, overlap=overlap)
