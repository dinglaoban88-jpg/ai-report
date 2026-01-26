from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable, List


def _clean_for_display(text: str) -> str:
    """清理文本，移除元数据噪音"""
    if not text:
        return ""
    cleaned = text
    # 移除时间戳
    cleaned = re.sub(r'\d+\s*(days?|hours?|minutes?)\s*ago', '', cleaned, flags=re.I)
    # 移除元数据
    cleaned = re.sub(r'(Discussion|Comments?|Link|Source:)[^\n]*', '', cleaned, flags=re.I)
    # 移除多余空白
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def generate_markdown(products: Iterable[dict], curated: Iterable[dict]) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    lines: List[str] = [f"**📅 [{today}] AI 效率日报**", ""]

    lines.append("**🚀 Part 1: 今日新品雷达**")
    products_list = list(products)
    if not products_list:
        lines.append("> 📉 今日暂无重大新品")
    else:
        for item in products_list:
            name = item.get("name", "").strip()
            summary = _clean_for_display(item.get("tagline", ""))
            url = item.get("url", "")
            # 简化输出，移除 Source 标签
            if summary:
                lines.append(f"- **{name}** - {summary} [🔗]({url})")
            else:
                lines.append(f"- **{name}** [🔗]({url})")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("**💼 Part 2: AI 产品精选每日推荐**")
    lines.append("")
    curated_list = list(curated)
    if not curated_list:
        lines.append("今日暂无精选推荐")
    else:
        for idx, item in enumerate(curated_list, 1):
            name = item.get("name", "").strip()
            reason = _clean_for_display(item.get("one_sentence_intro_cn", ""))
            url = item.get("url", "")
            source = item.get("source", "")
            origin = item.get("origin", "Global")
            # 【产地标签】
            origin_tag = "[🇨🇳 中国]" if origin == "CN" else "[🌍 海外]"
            # 【视觉层级】标题+产地 → 来源 → 理由 → 链接
            lines.append(f"**{idx}. {name}** {origin_tag}")
            if source:
                lines.append(f"*来源: {source}*")
            if reason:
                lines.append(f"💡 推荐理由: {reason}")
            if url:
                lines.append(f"🔗 [直达链接]({url})")
            lines.append("")  # 产品之间空一行

    return "\n".join(lines)
