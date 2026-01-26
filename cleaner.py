from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional

import pandas as pd


def parse_relative_time(text: str) -> Optional[datetime]:
    if not text:
        return None
    text = text.strip().lower()
    now = datetime.now(timezone.utc)
    if "today" in text or "今天" in text:
        return now
    if "yesterday" in text or "昨天" in text:
        return now - timedelta(days=1)
    hour_match = re.search(r"(\d+)\s*(hour|小时)", text)
    if hour_match:
        return now - timedelta(hours=int(hour_match.group(1)))
    min_match = re.search(r"(\d+)\s*(min|minute|分钟)", text)
    if min_match:
        return now - timedelta(minutes=int(min_match.group(1)))
    day_match = re.search(r"(\d+)\s*(day|天)", text)
    if day_match:
        return now - timedelta(days=int(day_match.group(1)))
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if date_match:
        try:
            return datetime.fromisoformat(date_match.group(1)).replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def filter_recent(items: Iterable[dict], max_hours: int = 48) -> List[dict]:
    rows = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_hours)
    for item in items:
        published_at = item.get("published_at")
        if not published_at and item.get("raw_date"):
            published_at = parse_relative_time(item.get("raw_date", ""))
        if published_at and published_at >= cutoff:
            item["published_at"] = published_at
            rows.append(item)
    return rows


def deduplicate(items: Iterable[dict]) -> List[dict]:
    df = pd.DataFrame(items)
    if df.empty:
        return []
    df["key"] = (
        df["name"].fillna("").str.lower().str.strip()
        + "|"
        + df["url"].fillna("").str.lower().str.strip()
    )
    df = df.drop_duplicates(subset=["key"])
    return df.drop(columns=["key"]).to_dict(orient="records")


def parse_traffic_value(value: str) -> float:
    if not value:
        return 0.0
    value = value.replace(",", "").lower()
    match = re.search(r"([\d.]+)\s*([km]?)", value)
    if not match:
        return 0.0
    number = float(match.group(1))
    suffix = match.group(2)
    if suffix == "k":
        return number * 1_000
    if suffix == "m":
        return number * 1_000_000
    return number


def apply_aicpb_filter(
    items: Iterable[dict],
    rankings: Iterable[dict],
    low_traffic_threshold: float = 2000.0,
    tail_ratio: float = 0.2,
) -> List[dict]:
    rankings_list = list(rankings)
    if not rankings_list:
        return list(items)

    df = pd.DataFrame(rankings_list)
    df["traffic_value"] = df["traffic"].apply(parse_traffic_value)
    df = df.sort_values("traffic_value")
    tail_size = max(1, int(len(df) * tail_ratio))
    tail = df.head(tail_size)
    low_tail = tail[tail["traffic_value"] <= low_traffic_threshold]
    zombie_names = set(low_tail["name"].str.lower().str.strip())

    filtered = []
    for item in items:
        name = item.get("name", "").lower().strip()
        if name in zombie_names:
            continue
        filtered.append(item)
    return filtered


def select_top(items: Iterable[dict], limit: int = 10) -> List[dict]:
    df = pd.DataFrame(items)
    if df.empty:
        return []
    df = df.sort_values("published_at", ascending=False)
    return df.head(limit).to_dict(orient="records")
