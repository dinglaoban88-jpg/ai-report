from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import List, Optional

import feedparser
import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _to_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _strip_html(text: str) -> str:
    if not text:
        return ""
    soup = BeautifulSoup(text, "html.parser")
    cleaned = soup.get_text(" ", strip=True)
    cleaned = re.sub(r"\s*Discussion\s*\|\s*Link.*$", "", cleaned).strip()
    return cleaned


def _clean_ph_title(title: str) -> str:
    if not title:
        return ""
    return re.sub(r"\s*-\s*.*?(Discussion|Link).*$", "", title).strip()


def _parse_rss_datetime(entry) -> Optional[datetime]:
    if entry.get("published_parsed"):
        try:
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        except Exception:
            return None
    return _to_datetime(entry.get("published", ""))


def fetch_product_hunt_rss(limit: int = 30) -> List[dict]:
    feed = feedparser.parse("https://www.producthunt.com/feed")
    items: List[dict] = []
    for entry in feed.entries[:limit]:
        raw_title = (entry.get("title") or "").strip()
        name = _clean_ph_title(raw_title)
        link = (entry.get("link") or "").strip()
        if not name or not link:
            continue
        published_at = _parse_rss_datetime(entry)
        tagline = _strip_html(entry.get("summary") or "").strip()
        items.append(
            {
                "name": name,
                "url": link,
                "tagline": tagline,
                "published_at": published_at,
                "source": "Product Hunt",
            }
        )
    return items


def _extract_toolify_jina(text: str, kind: str) -> List[tuple[str, str]]:
    if "Markdown Content:" not in text:
        return []
    content = text.split("Markdown Content:", 1)[-1]
    if kind == "sitemap":
        return [
            (match.group(1), "")
            for match in re.finditer(r"(https?://www\.toolify\.ai/\S+)", content)
            if "sitemap_tools" in match.group(1)
        ]
    return [
        (match.group(1), match.group(2))
        for match in re.finditer(
            r"(https?://www\.toolify\.ai/(?:zh/)?tool/\S+)\s+(\d{4}-\d{2}-\d{2})",
            content,
        )
    ]


def fetch_toolify_sitemap(limit: int = 50) -> List[dict]:
    """抓取 Toolify 工具列表 - 优先使用 HTML 页面，备选 Sitemap"""
    items = _fetch_toolify_html(limit)
    if items:
        return items
    # Fallback to sitemap via Jina
    return _fetch_toolify_sitemap_jina(limit)


def _fetch_toolify_html(limit: int = 50) -> List[dict]:
    """直接抓取 Toolify 新工具 HTML 页面"""
    items: List[dict] = []
    headers = {"User-Agent": USER_AGENT}
    # 尝试多个页面
    urls_to_try = [
        "https://www.toolify.ai/ai-tools",
        "https://www.toolify.ai/new-ai-tools",
    ]
    seen_urls = set()
    seen_names = set()
    for page_url in urls_to_try:
        try:
            resp = requests.get(page_url, headers=headers, timeout=25)
            if resp.status_code >= 400:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            # 寻找工具卡片链接
            for link in soup.select('a[href*="/tool/"]'):
                if len(items) >= limit:
                    break
                href = link.get("href", "")
                name = link.get_text(strip=True)
                if not name or len(name) < 2 or not href:
                    continue
                # 规范化 URL
                if href.startswith("/"):
                    href = f"https://www.toolify.ai{href}"
                # 【双重去重】URL 和 名称
                url_key = href.lower().split("?")[0]
                name_key = name.lower().strip()
                if url_key in seen_urls or name_key in seen_names:
                    continue
                seen_urls.add(url_key)
                seen_names.add(name_key)
                items.append({
                    "name": name,
                    "url": url_key,
                    "tagline": "",
                    "published_at": datetime.now(timezone.utc),
                    "source": "Toolify",
                })
            if items:
                break
        except Exception:
            continue
    return items


def _fetch_toolify_sitemap_jina(limit: int = 50) -> List[dict]:
    """通过 Jina 代理抓取 Toolify Sitemap (备选方案)"""
    urls = []
    sitemap_urls = [
        ("https://www.toolify.ai/sitemap_tools_1.xml", ""),
        ("https://www.toolify.ai/sitemap_tools_2.xml", ""),
        ("https://www.toolify.ai/sitemap_tools_3.xml", ""),
        ("https://www.toolify.ai/sitemap_tools_4.xml", ""),
    ]
    for link, _ in sitemap_urls[:4]:
        try:
            idx = requests.get(
                f"https://r.jina.ai/http://{link.replace('https://', '')}", timeout=25
            )
            idx.raise_for_status()
        except Exception:
            continue
        tool_links = _extract_toolify_jina(idx.text, "tool")
        for tool_link, lastmod in tool_links:
            published_at = _to_datetime(lastmod)
            urls.append((tool_link, published_at, lastmod))
            if len(urls) >= limit:
                break
        if len(urls) >= limit:
            break
    items = []
    for link, published_at, lastmod in urls:
        name = link.rstrip("/").split("/")[-1].replace("-", " ")
        items.append({
            "name": name,
            "url": link,
            "tagline": "",
            "published_at": published_at,
            "source": "Toolify",
            "raw_date": lastmod,
        })
    return items


def fetch_hacker_news_ai(limit: int = 40) -> List[dict]:
    """只抓取 Show HN 中的 AI 工具发布，过滤掉新闻/故事/非AI内容"""
    # 【叙事性标题黑名单】这些是故事/新闻，不是产品发布
    HN_STORY_BLOCKLIST = {
        "firmware", "intelligence", "security", "police", "hack", "hacked", "hacking",
        "story", "how i", "why i", "what i", "my experience", "lessons learned",
        "detained", "arrested", "incident", "breach", "leak", "vulnerability",
        "exploit", "reverse engineer", "investigation", "research paper",
        "lighthouse", "navigation", "swiss", "government", "politics",
    }
    # 【AI 关键词白名单】标题必须包含这些 AI 相关词才算
    AI_KEYWORDS = {"ai", "gpt", "llm", "ml", "machine learning", "chatbot", "neural", "openai", "claude", "gemini"}
    # 【产品发布白名单】优先保留这些标题模式
    HN_LAUNCH_WHITELIST = {"show hn", "launch", "release", "introducing", "announcing"}
    
    items: List[dict] = []
    try:
        ids = requests.get(
            "https://hacker-news.firebaseio.com/v0/showstories.json", timeout=15
        ).json()
    except Exception:
        return items
    for story_id in ids[:200]:
        try:
            data = requests.get(
                f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json", timeout=15
            ).json()
        except Exception:
            continue
        title = (data.get("title") or "").strip()
        title_lower = title.lower()
        
        # 必须包含 AI 相关词（作为独立单词，避免误匹配如 "aids"）
        has_ai = any(
            re.search(rf'\b{kw}\b', title_lower) 
            for kw in AI_KEYWORDS
        )
        if not has_ai:
            continue
        
        # 【去新闻化过滤】叙事性标题直接丢弃
        if any(block in title_lower for block in HN_STORY_BLOCKLIST):
            continue
        
        # 优先保留明确的产品发布
        is_launch = any(launch in title_lower for launch in HN_LAUNCH_WHITELIST)
        
        url = data.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
        published_at = datetime.fromtimestamp(data.get("time", 0), tz=timezone.utc)
        
        # 【清洗标题】移除 Show HN / Launch HN / Ask HN 前缀
        clean_title = re.sub(r'^(Show HN|Launch HN|Ask HN):\s*', '', title, flags=re.IGNORECASE)
        # 移除多余的引号和破折号
        clean_title = re.sub(r'^["\'\-–—]\s*', '', clean_title).strip()
        
        items.append(
            {
                "name": clean_title,
                "url": url,
                "tagline": clean_title,
                "published_at": published_at,
                "source": "Hacker News",
                "is_launch": is_launch,  # 标记是否为产品发布
            }
        )
        if len(items) >= limit:
            break
    return items


# 负向关键词：过滤教程、课程、资源合集等非工具类项目
GITHUB_BLOCKLIST = {
    "tutorial", "tutorials", "course", "courses", "bootcamp", "bootcamps",
    "learning", "learn", "study", "studying", "lesson", "lessons",
    "demo", "demos", "example", "examples", "sample", "samples",
    "collection", "collections", "awesome-", "curated", "list-of",
    "handbook", "guide", "guides", "cheatsheet", "cheat-sheet",
    "interview", "interviews", "exercises", "practice", "training",
    "workshop", "workshops", "resources", "教程", "课程", "学习",
}


def _is_github_courseware(name: str, description: str) -> bool:
    """检查是否为教程/课程/资源合集类项目（非工具）"""
    haystack = f"{name} {description}".lower()
    for keyword in GITHUB_BLOCKLIST:
        if keyword in haystack:
            return True
    # 额外检查：awesome-* 前缀
    if name.lower().startswith("awesome-"):
        return True
    return False


# 开发者工具黑名单：这些是代码库/框架，不是普通用户能用的应用
GITHUB_DEV_BLOCKLIST = {
    "sdk", "api", "cli", "library", "framework", "boilerplate", "template",
    "starter", "scaffold", "backend", "frontend", "server", "client",
    "database", "db", "orm", "driver", "connector", "adapter",
    "kubernetes", "k8s", "docker", "container", "helm", "terraform",
    "ci", "cd", "pipeline", "devops", "infra", "infrastructure",
    "package", "module", "component", "plugin", "extension",
    "binding", "wrapper", "integration", "middleware",
}


def _is_github_dev_tool(name: str, description: str, topics: list) -> bool:
    """检查是否为开发者工具/代码库（普通用户无法直接使用）"""
    haystack = f"{name} {description}".lower()
    topics_str = " ".join(topics).lower() if topics else ""
    
    # 检查开发者工具关键词
    for keyword in GITHUB_DEV_BLOCKLIST:
        if keyword in haystack or keyword in topics_str:
            return True
    
    # 如果没有 GUI/App/Desktop 相关词，且没有 no-code/low-code，可能是代码库
    gui_indicators = {"app", "desktop", "gui", "web app", "webapp", "saas", "no-code", "nocode", "low-code", "lowcode"}
    has_gui = any(ind in haystack or ind in topics_str for ind in gui_indicators)
    
    # 如果明确是 Python/JS/Rust 等语言的库，剔除
    lib_patterns = ["python", "node", "npm", "rust", "go ", "golang", "java ", "ruby", "php"]
    is_lib = any(p in haystack for p in lib_patterns) and not has_gui
    
    return is_lib


def _clean_github_description(desc: str) -> str:
    """清理 GitHub 描述，移除徽章、emoji 和无用前缀"""
    if not desc:
        return ""
    cleaned = desc
    # 移除开头的 emoji
    cleaned = re.sub(r'^[\U0001F300-\U0001F9FF\U00002600-\U000027BF\s]+', '', cleaned)
    # 移除 [badge] 或 ![badge](url) 格式
    cleaned = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', cleaned)
    cleaned = re.sub(r'\[[^\]]*\]\([^)]*\)', '', cleaned)
    # 移除 HTML 标签
    cleaned = re.sub(r'<[^>]+>', '', cleaned)
    # 移除多余空白
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    # 移除开头的冒号（常见于 emoji 后）
    cleaned = re.sub(r'^[:\s]+', '', cleaned)
    return cleaned


def fetch_github_ai(limit: int = 40) -> List[dict]:
    items: List[dict] = []
    headers = {"Accept": "application/vnd.github+json"}
    token = None
    try:
        from config_manager import load_config
        token = (load_config().get("github_token") or "").strip()
    except Exception:
        token = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    since = (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()
    
    # 尝试多个查询，增加超时
    queries = [
        f"topic:ai created:>{since}",
        f"topic:llm created:>{since}",
        "topic:ai-tools sort:updated",
    ]
    data = {"items": []}
    for query in queries:
        url = f"https://api.github.com/search/repositories?q={query}&sort=stars&order=desc&per_page=20"
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                break
        except Exception:
            continue
    if not data.get("items"):
        return items
    for repo in data.get("items", []):
        if len(items) >= limit:
            break
        name = repo.get("name") or ""
        full_name = repo.get("full_name") or name
        repo_url = repo.get("html_url") or ""
        raw_desc = repo.get("description") or ""
        topics = repo.get("topics", [])
        # 过滤教程/课程/资源合集
        if _is_github_courseware(name, raw_desc):
            continue
        # 过滤开发者工具/代码库（非应用）
        if _is_github_dev_tool(name, raw_desc, topics):
            continue
        # 清理描述，移除徽章和 emoji
        desc = _clean_github_description(raw_desc)
        # 获取 topics 作为补充信息
        topics_str = ", ".join(topics[:5]) if topics else ""
        # 如果描述太短，尝试用 topics 补充
        if len(desc) < 20 and topics_str:
            desc = f"{desc} ({topics_str})" if desc else topics_str
        published_at = _to_datetime(repo.get("created_at", ""))
        items.append(
            {
                "name": full_name,
                "url": repo_url,
                "tagline": desc,
                "published_at": published_at,
                "source": "GitHub",
                "stars": repo.get("stargazers_count", 0),
                "homepage": repo.get("homepage") or "",
                "topics": topics,
            }
        )
    return items


def fetch_taaft_timeline(limit: int = 40) -> List[dict]:
    """抓取 TAAFT 新工具列表（应用层 AI 工具）"""
    items: List[dict] = []
    headers = {"User-Agent": USER_AGENT}
    # 使用 /new/ 页面，数据更稳定
    url = "https://theresanaiforthat.com/new/"
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
    except Exception:
        return items
    soup = BeautifulSoup(resp.text, "html.parser")
    
    # 找所有 /ai/tool-name/ 链接
    seen_tools = set()
    links = soup.select('a[href*="/ai/"]')
    for link in links:
        if len(items) >= limit:
            break
        href = link.get("href", "")
        # 过滤：只要 /ai/xxx/ 格式，排除广告链接(ref=sponsor)
        if not href or "/ai/" not in href:
            continue
        if "ref=sponsor" in href or "ref=taaft" in href:
            continue
        # 提取工具名
        name = link.get_text(strip=True)
        # 过滤掉价格、数字、日期等无效名称
        if not name or len(name) < 2:
            continue
        name_lower = name.lower()
        # 过滤价格字符串
        if any(p in name_lower for p in ["free", "pricing", "from $", "/mo", "/yr", "$ ", "·"]):
            continue
        # 过滤日期和 @ 开头的 Twitter handles
        if name.startswith("@") or name.startswith("#"):
            continue
        # 过滤日期格式 (Oct 30, 2025)
        months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
        if any(m in name_lower for m in months) and any(c.isdigit() for c in name):
            continue
        # 过滤纯数字或以数字开头的
        if any(c.isdigit() for c in name[:3]):
            continue
        # 过滤版本号 (v1.1.0)
        if "v1" in name_lower or "v2" in name_lower or "v0" in name_lower:
            continue
        # 规范化 URL
        if href.startswith("/"):
            href = f"https://theresanaiforthat.com{href}"
        # 去重
        tool_key = name.lower().strip()
        if tool_key in seen_tools:
            continue
        seen_tools.add(tool_key)
        
        items.append(
            {
                "name": name,
                "url": href.split("?")[0],  # 移除查询参数
                "tagline": "",
                "published_at": datetime.now(timezone.utc),
                "source": "TAAFT",
            }
        )
    return items


def fetch_futurepedia(limit: int = 30) -> List[dict]:
    """抓取 Futurepedia 工具列表（应用层，非开发工具）
    
    注意：Futurepedia 新工具页面是 JS 渲染的，只能抓取主页的精选工具。
    大厂工具会被 Curator 的黑名单过滤掉。
    """
    items: List[dict] = []
    headers = {"User-Agent": USER_AGENT}
    url = "https://www.futurepedia.io/"
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
    except Exception:
        return items
    soup = BeautifulSoup(resp.text, "html.parser")
    
    # 大厂黑名单（这些会被 Curator 过滤，但在这里提前过滤能提高效率）
    giant_blocklist = {
        "chatgpt", "claude", "midjourney", "perplexity", "gemini", "grok", "copilot",
        "google gemini", "openai", "microsoft", "google", "meta", "amazon",
        "leonardo ai", "stable diffusion", "dall-e", "runway", "pika",
    }
    
    seen = set()
    tool_links = soup.select('a[href*="/tool/"]')
    for link in tool_links:
        if len(items) >= limit:
            break
        href = link.get("href", "")
        name = link.get_text(strip=True)
        if not name or len(name) < 2:
            continue
        # 过滤大厂
        if name.lower() in giant_blocklist:
            continue
        # 去重
        name_key = name.lower()
        if name_key in seen:
            continue
        seen.add(name_key)
        
        items.append(
            {
                "name": name,
                "url": href if href.startswith("http") else f"https://www.futurepedia.io{href}",
                "tagline": "",
                "published_at": datetime.now(timezone.utc),
                "source": "Futurepedia",
            }
        )
    return items
