from __future__ import annotations

import logging
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth


@dataclass
class ProductItem:
    name: str
    url: str
    tagline: str
    published_at: Optional[datetime]
    raw_date: str
    tags: List[str]
    reviews: int
    source: str
    category: str = ""


@dataclass
class NewsItem:
    title: str
    url: str
    summary: str
    source: str


class Scraper:
    def __init__(
        self,
        headless: bool = True,
        user_agent: str = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        timeout_ms: int = 30000,
        max_retries: int = 3,
        sleep_range: tuple[float, float] = (1.0, 2.5),
    ) -> None:
        self.headless = headless
        self.user_agent = user_agent
        self.timeout_ms = timeout_ms
        self.max_retries = max_retries
        self.sleep_range = sleep_range
        self._playwright = None
        self._browser = None
        self._context = None

    def __enter__(self) -> "Scraper":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def start(self) -> None:
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = self._browser.new_context(
            user_agent=self.user_agent,
            viewport={"width": 1280, "height": 720},
        )

    def _apply_stealth(self, page) -> None:
        try:
            Stealth(navigator_languages=True, navigator_vendor=True).apply_stealth_sync(page)
        except Exception:
            pass

    def close(self) -> None:
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def _new_page(self):
        if not self._context:
            raise RuntimeError("Playwright context not started")
        page = self._context.new_page()
        page.set_default_timeout(self.timeout_ms)
        self._apply_stealth(page)
        return page

    def _sleep_jitter(self) -> None:
        time.sleep(random.uniform(*self.sleep_range))

    def _with_retry(self, func, description: str):
        last_err = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return func()
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                wait = min(2**attempt, 10) + random.random()
                time.sleep(wait)
        raise RuntimeError(f"Failed after retries: {description}") from last_err

    def _safe_text(self, locator, selectors: Iterable[str]) -> str:
        for sel in selectors:
            try:
                node = locator.locator(sel).first
                text = node.text_content()
                if text:
                    return text.strip()
            except Exception:
                continue
        return ""

    def _safe_attr(self, locator, selectors: Iterable[str], attr: str) -> str:
        for sel in selectors:
            try:
                node = locator.locator(sel).first
                value = node.get_attribute(attr)
                if value:
                    return value.strip()
            except Exception:
                continue
        return ""

    @staticmethod
    def _is_cloudflare_blocked(html: str) -> bool:
        if not html:
            return False
        lowered = html.lower()
        return "cloudflare" in lowered and ("verify" in lowered or "checking your browser" in lowered)

    def _fetch_via_jina(self, url: str) -> str:
        if not url:
            return ""
        if url.startswith("https://"):
            proxy = f"https://r.jina.ai/http://{url[len('https://'):]}"
        elif url.startswith("http://"):
            proxy = f"https://r.jina.ai/http://{url[len('http://'):]}"
        else:
            proxy = f"https://r.jina.ai/http://{url}"
        try:
            resp = requests.get(proxy, timeout=20)
            if resp.status_code < 400:
                return resp.text
        except Exception:
            return ""
        return ""

    def _scrape_product_hunt_graphql(self, limit: int = 10) -> List[ProductItem]:
        token = os.getenv("PH_API_TOKEN", "").strip()
        if not token:
            try:
                from config_manager import load_config

                token = (load_config().get("ph_api_token") or "").strip()
            except Exception:
                token = ""
        if not token:
            return []
        url = "https://api.producthunt.com/v2/api/graphql"
        query = """
        query NewestPosts($first: Int!) {
          posts(order: NEWEST, first: $first) {
            edges {
              node {
                name
                tagline
                url
                publishedAt
              }
            }
          }
        }
        """
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        payload = {"query": query, "variables": {"first": limit}}
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
        edges = (
            data.get("data", {})
            .get("posts", {})
            .get("edges", [])
        )
        results: List[ProductItem] = []
        for edge in edges:
            node = edge.get("node") or {}
            name = (node.get("name") or "").strip()
            link = (node.get("url") or "").strip()
            if not name or not link:
                continue
            published_at = None
            raw_date = ""
            if node.get("publishedAt"):
                raw_date = node.get("publishedAt")
                try:
                    published_at = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                except Exception:
                    published_at = None
            results.append(
                ProductItem(
                    name=name,
                    url=link,
                    tagline=(node.get("tagline") or "").strip(),
                    published_at=published_at,
                    raw_date=raw_date,
                    tags=[],
                    reviews=0,
                    source="Product Hunt",
                )
            )
        return results

    def _scrape_product_hunt_rss_feed(self, limit: int = 10) -> List[ProductItem]:
        feed_url = "https://www.producthunt.com/feed"
        feed = feedparser.parse(feed_url)
        results: List[ProductItem] = []
        for entry in feed.entries[:limit]:
            name = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            if not name or not link:
                continue
            published_at = None
            raw_date = entry.get("published") or ""
            if raw_date:
                try:
                    published_at = datetime.fromisoformat(raw_date)
                except Exception:
                    published_at = None
            results.append(
                ProductItem(
                    name=name,
                    url=link,
                    tagline=(entry.get("summary") or "").strip(),
                    published_at=published_at,
                    raw_date=raw_date,
                    tags=[],
                    reviews=0,
                    source="Product Hunt",
                )
            )
        return results

    def _parse_relative_time(self, text: str) -> Optional[datetime]:
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

    def validate_is_new(self, date_str: Optional[str], max_hours: int = 24) -> bool:
        if not date_str:
            return False
        dt = self._parse_relative_time(date_str)
        if not dt:
            return False
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_hours)
        return dt >= cutoff

    def _is_recent_item(self, item: ProductItem, max_hours: int = 24) -> bool:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max_hours)
        if item.published_at and item.published_at >= cutoff:
            return True
        if self.validate_is_new(item.raw_date, max_hours=max_hours):
            item.published_at = self._parse_relative_time(item.raw_date)
            return True
        return False

    def _extract_cards_by_link(
        self,
        page,
        href_contains: str,
        skip_titles: set[str],
    ) -> list[dict]:
        script = """
        (hrefSub, skipTitles) => {
            const skipSet = new Set(skipTitles || []);
            const root = document.querySelector('main') || document.body;
            const anchors = Array.from(root.querySelectorAll(`a[href*='${hrefSub}']`));
            const items = [];
            for (const a of anchors) {
                const card = a.closest('article, li, section, div') || a;
                const h = card.querySelector('h3, h4, h5') || a;
                const title = (h?.innerText || '').trim();
                if (!title || skipSet.has(title.toLowerCase())) {
                    continue;
                }
                const desc = (card.querySelector('p')?.innerText || '').trim();
                items.push({
                    title,
                    href: a.href,
                    desc,
                });
            }
            return items;
        }
        """
        return page.evaluate(script, href_contains, list(skip_titles))

    def _extract_links_in_main(
        self,
        page,
        href_substrings: list[str],
        skip_titles: set[str],
    ) -> list[dict]:
        script = """
        (subs, skipTitles) => {
            const skipSet = new Set(skipTitles || []);
            const root = document.querySelector('main') || document.body;
            const anchors = Array.from(root.querySelectorAll('a[href]'));
            const items = [];
            for (const a of anchors) {
                const href = a.href || '';
                if (!subs.some(s => href.includes(s))) {
                    continue;
                }
                const card = a.closest('article, li, section, div') || a;
                const h = card.querySelector('h3, h4, h5') || a;
                const title = (h?.innerText || '').trim();
                if (!title || skipSet.has(title.toLowerCase())) {
                    continue;
                }
                const desc = (card.querySelector('p')?.innerText || '').trim();
                items.push({ title, href, desc });
            }
            return items;
        }
        """
        return page.evaluate(script, href_substrings, list(skip_titles))

    def _extract_cards_by_heading(self, page, limit: int, skip_titles: set[str]) -> list[dict]:
        script = """
        (maxItems, skipTitles) => {
            const skipSet = new Set(skipTitles || []);
            const root = document.querySelector('main') || document.body;
            const headers = Array.from(root.querySelectorAll('h3, h4')).slice(0, maxItems * 6);
            const items = [];
            for (const h of headers) {
                const title = (h.innerText || '').trim();
                if (!title || skipSet.has(title.toLowerCase())) {
                    continue;
                }
                const card = h.closest('article, li, section, div') || h.parentElement;
                const link = card ? card.querySelector('a[href]') : null;
                const href = link ? link.href : '';
                if (!href) {
                    continue;
                }
                const desc = (card?.querySelector('p')?.innerText || '').trim();
                items.push({ title, href, desc });
                if (items.length >= maxItems) {
                    break;
                }
            }
            return items;
        }
        """
        return page.evaluate(script, limit, list(skip_titles))

    def _extract_cards_generic(self, page, limit: int, skip_titles: set[str]) -> list[dict]:
        script = """
        (maxItems, skipTitles) => {
            const skipSet = new Set(skipTitles || []);
            const root = document.querySelector('main') || document.body;
            const cards = Array.from(root.querySelectorAll('article, li, section, div'));
            const items = [];
            for (const card of cards) {
                const h = card.querySelector('h3, h4');
                const p = card.querySelector('p');
                const a = card.querySelector('a[href]');
                if (!h || !a) {
                    continue;
                }
                const title = (h.innerText || '').trim();
                if (!title || skipSet.has(title.toLowerCase())) {
                    continue;
                }
                const href = a.href || '';
                if (!href) {
                    continue;
                }
                const desc = (p?.innerText || '').trim();
                items.push({ title, href, desc });
                if (items.length >= maxItems) {
                    break;
                }
            }
            return items;
        }
        """
        return page.evaluate(script, limit, list(skip_titles))

    def _extract_aibase_cards_from_html(self, html: str, limit: int) -> List[ProductItem]:
        soup = BeautifulSoup(html, "html.parser")
        results: List[ProductItem] = []
        skip_titles = {"english", "中文", "首页", "home", "资讯", "登录"}
        seen = set()
        for card in soup.select("article, li, div[class*='card'], div[class*='Card']"):
            title_node = card.select_one("h3, h4")
            link = card.select_one("a[href]")
            title = ""
            if title_node:
                title = title_node.get_text(strip=True)
            elif link:
                title = link.get_text(strip=True)
            if not title or title.lower() in skip_titles:
                continue
            if not link:
                continue
            href = link.get("href", "").strip()
            if not href:
                continue
            if href.startswith("//"):
                href = f"https:{href}"
            elif href.startswith("/"):
                href = f"https://app.aibase.com{href}"
            elif not href.startswith("http"):
                continue
            if "aibase.com" not in href or "course.aibase.com" in href:
                continue
            if href in seen:
                continue
            seen.add(href)
            desc_node = card.select_one("p")
            desc = desc_node.get_text(strip=True) if desc_node else ""
            results.append(
                ProductItem(
                    name=title,
                    url=href,
                    tagline=desc,
                    published_at=None,
                    raw_date="",
                    tags=[],
                    reviews=0,
                    source="AIBase",
                )
            )
            if len(results) >= limit:
                break
        return results

    def _scrape_product_hunt_list(
        self,
        base_url: str,
        pages: int = 1,
        require_day_header: bool = False,
    ) -> List[ProductItem]:
        results: List[ProductItem] = []

        def _build_paged_url(url: str, page_num: int) -> str:
            parts = urlsplit(url)
            query = dict(parse_qsl(parts.query))
            query["page"] = str(page_num)
            return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

        def _scrape_page(page_num: int):
            page = self._new_page()
            url = _build_paged_url(base_url, page_num)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            try:
                page.wait_for_selector(
                    "main [data-test='topic-post-item'], main [data-test='post-item'], main article",
                    timeout=8000,
                )
            except PlaywrightTimeoutError:
                pass
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1000)
            except Exception:
                pass

            main = page.locator("main")
            scope = main
            if require_day_header:
                day_header = main.locator("[data-test='day-header']")
                if day_header.count() == 0:
                    page.close()
                    return
                container = day_header.first.locator("xpath=ancestor::*[self::section or self::div][1]")
                if container.count() > 0:
                    scope = container
            card_selectors = [
                '[data-test="topic-post-item"]',
                '[data-test="post-item"]',
                '[data-test="post-item-v2"]',
                "div[class*='item_']",
                "div[class*='Item_']",
                "div[class*='styles_item__']",
                "article",
            ]
            cards = None
            for sel in card_selectors:
                loc = scope.locator(sel)
                if loc.count() > 0:
                    cards = loc
                    break
            if cards is None:
                # Fallback: use post links in main
                post_links = scope.locator("a[href*='/posts/']")
                if post_links.count() == 0:
                    post_links = page.locator("a[href*='/posts/']")
                for idx in range(post_links.count()):
                    link_node = post_links.nth(idx)
                    href = link_node.get_attribute("href") or ""
                    if href.startswith("/"):
                        href = f"https://www.producthunt.com{href}"
                    if "/posts/" not in href:
                        continue
                    card = link_node.locator("xpath=ancestor::*[self::article or self::div][1]")
                    name = self._safe_text(card, ["h3", "h4", "a"])
                    if not name:
                        name = link_node.text_content() or ""
                    name = name.strip()
                    if not name:
                        continue
                    tagline = self._safe_text(card, ["p", "div"])
                    results.append(
                        ProductItem(
                            name=name,
                            url=href,
                            tagline=tagline,
                            published_at=None,
                            raw_date="",
                            tags=[],
                            reviews=0,
                            source="Product Hunt",
                        )
                    )
                    if len(results) >= 20:
                        break
                page.close()
                return

            for idx in range(cards.count()):
                card = cards.nth(idx)
                name = self._safe_text(card, ["a[data-test='post-name']", "h3 a", "h3", "a"])
                if not name:
                    continue
                tagline = self._safe_text(
                    card,
                    ["div[data-test='post-tagline']", "p", "span[data-test='post-tagline']"],
                )
                link = self._safe_attr(card, ["a[data-test='post-name']", "h3 a", "a"], "href")
                if link and link.startswith("/"):
                    link = f"https://www.producthunt.com{link}"
                if link and "/posts/" not in link:
                    continue
                topics = []
                try:
                    topic_nodes = card.locator("a[href^='/topics/']")
                    for i in range(topic_nodes.count()):
                        txt = topic_nodes.nth(i).text_content()
                        if txt:
                            topics.append(txt.strip())
                except Exception:
                    pass

                raw_text = ""
                try:
                    raw_text = card.text_content() or ""
                except Exception:
                    pass
                reviews = 0
                review_match = re.search(r"(\d+)\s*(review|comment|discussion)", raw_text, re.I)
                if review_match:
                    reviews = int(review_match.group(1))

                time_text = self._safe_text(card, ["time", "span[title*='ago']", "span"])
                published_at = self._parse_relative_time(time_text)

                results.append(
                    ProductItem(
                        name=name,
                        url=link or "",
                        tagline=tagline,
                        published_at=published_at,
                        raw_date=time_text,
                        tags=topics,
                        reviews=reviews,
                        source="Product Hunt",
                    )
                )
            page.close()

        for page_num in range(1, pages + 1):
            self._with_retry(lambda: _scrape_page(page_num), f"Product Hunt page {page_num}")
            self._sleep_jitter()

        return results

    def _scrape_product_hunt_home_today(self, limit: int = 20) -> List[ProductItem]:
        results: List[ProductItem] = []

        def _scrape():
            page = self._new_page()
            page.goto("https://www.producthunt.com/", wait_until="domcontentloaded")
            try:
                page.wait_for_selector("[data-test='product-item']", timeout=15000)
            except PlaywrightTimeoutError:
                page.wait_for_timeout(5000)
            cards = page.locator("[data-test='product-item']")
            for idx in range(cards.count()):
                card = cards.nth(idx)
                name = self._safe_text(card, ["a[data-test='product-name']", "h3", "a"])
                if not name:
                    continue
                tagline = self._safe_text(card, ["p", "div[data-test='product-tagline']"])
                link = self._safe_attr(card, ["a[href*='/posts/']", "a"], "href")
                if link and link.startswith("/"):
                    link = f"https://www.producthunt.com{link}"
                if link and "/posts/" not in link:
                    continue
                results.append(
                    ProductItem(
                        name=name,
                        url=link or "",
                        tagline=tagline,
                        published_at=self._parse_relative_time("today"),
                        raw_date="today",
                        tags=[],
                        reviews=0,
                        source="Product Hunt",
                    )
                )
                if len(results) >= limit:
                    break
            page.close()
            if results:
                return results

            # Cloudflare fallback via Jina AI proxy
            html = self._fetch_via_jina("https://www.producthunt.com/")
            if self._is_cloudflare_blocked(html):
                return results
            soup = BeautifulSoup(html, "html.parser")
            for card in soup.select("[data-test='product-item']"):
                name = (card.select_one("[data-test='product-name']") or card.select_one("h3") or card.select_one("a"))
                name_text = name.get_text(strip=True) if name else ""
                if not name_text:
                    continue
                tagline_node = card.select_one("p") or card.select_one("[data-test='product-tagline']")
                tagline = tagline_node.get_text(strip=True) if tagline_node else ""
                link_node = card.select_one("a[href*='/posts/']") or card.select_one("a[href]")
                link = ""
                if link_node and link_node.get("href"):
                    href = link_node["href"].strip()
                    link = f"https://www.producthunt.com{href}" if href.startswith("/") else href
                if link and "/posts/" not in link:
                    continue
                results.append(
                    ProductItem(
                        name=name_text,
                        url=link,
                        tagline=tagline,
                        published_at=self._parse_relative_time("today"),
                        raw_date="today",
                        tags=[],
                        reviews=0,
                        source="Product Hunt",
                    )
                )
                if len(results) >= limit:
                    break
            return results

        return self._with_retry(_scrape, "Product Hunt home")

    def _scrape_product_hunt_rss(self, rss_url: str, limit: int = 20) -> List[ProductItem]:
        results: List[ProductItem] = []

        def _scrape():
            headers = {
                "User-Agent": self.user_agent,
                "Accept": "application/rss+xml,application/xml,text/xml,*/*",
            }
            try:
                resp = requests.get(rss_url, headers=headers, timeout=15)
            except Exception:
                resp = None
            if not resp or resp.status_code >= 400:
                proxy = f"https://r.jina.ai/{rss_url}"
                resp = requests.get(proxy, headers=headers, timeout=20)
            if resp.status_code >= 400:
                return []
            soup = BeautifulSoup(resp.text, "xml")
            for item in soup.find_all("item"):
                title = (item.find("title").get_text(strip=True) if item.find("title") else "")
                link = (item.find("link").get_text(strip=True) if item.find("link") else "")
                desc = (item.find("description").get_text(strip=True) if item.find("description") else "")
                if not title or not link:
                    continue
                results.append(
                    ProductItem(
                        name=title,
                        url=link,
                        tagline=desc,
                        published_at=None,
                        raw_date="",
                        tags=[],
                        reviews=0,
                        source="Product Hunt",
                    )
                )
                if len(results) >= limit:
                    break
            return results

        try:
            return self._with_retry(_scrape, "Product Hunt RSS")
        except Exception:
            return []

    def scrape_product_hunt_today(self, limit: int = 20) -> List[ProductItem]:
        items = self._scrape_product_hunt_graphql(limit=limit)
        if items:
            fresh = [item for item in items if self._is_recent_item(item, max_hours=24)]
            if fresh:
                return fresh[:limit]

        rss_items = self._scrape_product_hunt_rss_feed(limit=limit)
        if rss_items:
            fresh = [item for item in rss_items if self._is_recent_item(item, max_hours=24)]
            if fresh:
                return fresh[:limit]
        return []

    def scrape_product_hunt_trending(self, limit: int = 10) -> List[ProductItem]:
        base_url = "https://www.producthunt.com/topics/productivity"
        items = self._scrape_product_hunt_list(base_url, pages=1)
        return items[:limit]

    def scrape_product_hunt_trending_weekly(self, limit: int = 10) -> List[ProductItem]:
        base_url = "https://www.producthunt.com/topics/productivity?time=week"
        items = self._scrape_product_hunt_list(base_url, pages=1)
        return items[:limit]

    def scrape_product_hunt_trending_monthly(self, limit: int = 10) -> List[ProductItem]:
        base_url = "https://www.producthunt.com/topics/productivity?time=month"
        items = self._scrape_product_hunt_list(base_url, pages=1)
        return items[:limit]

    def _scrape_toolify_section(self, url: str, keywords: list[str], limit: int) -> List[ProductItem]:
        results: List[ProductItem] = []

        def _scrape():
            try:
                page = self._new_page()
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
                try:
                    page.wait_for_selector(
                        "main a[href*='/zh/tool/'], main a[href*='/tool/']", timeout=10000
                    )
                except PlaywrightTimeoutError:
                    pass
                skip_titles = {"english", "中文", "首页", "home", "资讯", "登录"}
                try:
                    items = []
                    for sub in ["/zh/tool/", "/tool/"]:
                        items.extend(self._extract_cards_by_link(page, sub, skip_titles))
                except Exception:
                    items = []
                page_html = (
                    page.locator("main").inner_html() if page.locator("main").count() else page.content()
                )
                page.close()
            except Exception:
                return results

            if self._is_cloudflare_blocked(page_html):
                proxy_html = self._fetch_via_jina(url)
                if proxy_html:
                    page_html = proxy_html

            soup = BeautifulSoup(page_html, "html.parser")
            sections = []
            for header in soup.find_all(["h2", "h3", "div"]):
                text = header.get_text(strip=True)
                if any(k in text for k in keywords):
                    sections.append(header)

            candidates = []
            tool_cards = soup.select("div.tool-item, div[class*='tool-item']")
            if tool_cards:
                for card in tool_cards:
                    handle = card.get("data-handle", "").strip()
                    href = f"https://www.toolify.ai/zh/tool/{handle}" if handle else ""
                    link = card.select_one("a[href*='/tool/']") or card.select_one("a[href]")
                    if link and link.get("href"):
                        raw_href = link.get("href").strip()
                        if raw_href.startswith("/"):
                            href = f"https://www.toolify.ai{raw_href}"
                        elif raw_href.startswith("http"):
                            href = raw_href
                    name_node = card.select_one("h3, h4, .tool-name, .tool-title")
                    name = name_node.get_text(strip=True) if name_node else ""
                    desc_node = card.select_one(".tool-desc, .tool-description, p")
                    desc = desc_node.get_text(strip=True) if desc_node else ""
                    text_blob = card.get_text(" ", strip=True)
                    raw_date = ""
                    date_match = re.search(r"\d{4}-\d{2}-\d{2}", text_blob)
                    if date_match:
                        raw_date = date_match.group(0)
                    else:
                        for key in ["Just Launched", "Today", "今天", "刚刚", "小时前"]:
                            if key in text_blob:
                                raw_date = key
                                break
                    if not name or name in {"首页", "AI资讯", "English", "繁體中文"}:
                        continue
                    if not href:
                        continue
                    results.append(
                        ProductItem(
                            name=name,
                            url=href,
                            tagline=desc,
                            published_at=self._parse_relative_time(raw_date),
                            raw_date=raw_date,
                            tags=[],
                            reviews=0,
                            source="Toolify",
                        )
                    )
                    if len(results) >= limit:
                        return results[:limit]

            if sections:
                for header in sections:
                    container = header.find_parent(["section", "div"]) or header
                    candidates.extend(container.select("a[href*='/tool/']"))
            else:
                candidates = soup.select(
                    "main a[href*='/tool/'], article a[href*='/tool/'], div[class*='card'] a[href*='/tool/']"
                )
                if not candidates:
                    candidates = soup.select("article a[href], div[class*='card'] a[href]")

            seen = set()
            for a in candidates:
                href = a.get("href", "").strip()
                if not href:
                    continue
                if href.startswith("/"):
                    full_url = f"https://www.toolify.ai{href}"
                elif href.startswith("http"):
                    full_url = href
                else:
                    full_url = f"https://www.toolify.ai/{href.lstrip('/')}"
                if full_url in seen:
                    continue
                seen.add(full_url)

                name = a.get_text(strip=True)
                card = a.find_parent(["article", "div", "li"]) or a
                if not name or name in {"首页", "AI资讯", "English", "繁體中文"}:
                    continue
                text_blob = card.get_text(" ", strip=True)
                raw_date = ""
                date_match = re.search(r"\d{4}-\d{2}-\d{2}", text_blob)
                if date_match:
                    raw_date = date_match.group(0)
                else:
                    for key in ["今天", "刚刚", "小时前", "Today", "Just Launched"]:
                        if key in text_blob:
                            raw_date = key
                            break

                category = ""
                cat_match = re.search(r"(分类|Category)\s*[:：]?\s*([^\s|/]+)", text_blob)
                if cat_match:
                    category = cat_match.group(2)

                ext_link = ""
                for visit_text in ["访问网站", "官网", "Visit", "Website"]:
                    visit_node = card.find("a", string=re.compile(visit_text))
                    if visit_node and visit_node.get("href"):
                        ext_link = visit_node["href"]
                        break

                results.append(
                    ProductItem(
                        name=name or "",
                        url=ext_link or full_url,
                        tagline="",
                        published_at=self._parse_relative_time(raw_date),
                        raw_date=raw_date,
                        tags=[],
                        reviews=0,
                        source="Toolify",
                        category=category,
                    )
                )

            if results:
                return results[:limit]

            # Fallback: try card-based extraction
            seen = set()
            seen = set()
            seen = set()
            seen = set()
            seen = set()
            seen = set()
            if items:
                for item in items:
                    name = item.get("title", "")
                    if not name:
                        continue
                    href = item.get("href", "")
                    if not href:
                        continue
                    if href in seen:
                        continue
                    seen.add(href)
                    results.append(
                        ProductItem(
                            name=name,
                            url=href,
                            tagline=item.get("desc", ""),
                            published_at=None,
                            raw_date="",
                            tags=[],
                            reviews=0,
                            source="Toolify",
                        )
                    )
                    if len(results) >= limit:
                        break

            if results:
                return results[:limit]

            cards = soup.select("main div[class*='card'], main article, main li")
            for card in cards:
                link = card.select_one("a[href*='/tool/']")
                if not link:
                    continue
                name = link.get_text(strip=True)
                if not name or name in {"首页", "AI资讯", "English", "繁體中文"}:
                    continue
                href = link.get("href", "").strip()
                if not href:
                    continue
                if href.startswith("/"):
                    full_url = f"https://www.toolify.ai{href}"
                elif href.startswith("http"):
                    full_url = href
                else:
                    full_url = f"https://www.toolify.ai/{href.lstrip('/')}"
                desc = ""
                p = card.find("p")
                if p:
                    desc = p.get_text(strip=True)
                results.append(
                    ProductItem(
                        name=name,
                        url=full_url,
                        tagline=desc,
                        published_at=None,
                        raw_date="",
                        tags=[],
                        reviews=0,
                        source="Toolify",
                    )
                )
                if len(results) >= limit:
                    break
            if results:
                return results[:limit]

            cards = soup.select("main div[class*='tool'], main div[class*='Tool']")
            for card in cards:
                link = card.select_one("a[href*='/tool/']")
                if not link:
                    continue
                name = link.get_text(strip=True)
                if not name or name in {"首页", "AI资讯", "English", "繁體中文"}:
                    continue
                href = link.get("href", "").strip()
                if not href:
                    continue
                if href.startswith("/"):
                    full_url = f"https://www.toolify.ai{href}"
                elif href.startswith("http"):
                    full_url = href
                else:
                    full_url = f"https://www.toolify.ai/{href.lstrip('/')}"
                desc = ""
                p = card.find("p")
                if p:
                    desc = p.get_text(strip=True)
                results.append(
                    ProductItem(
                        name=name,
                        url=full_url,
                        tagline=desc,
                        published_at=None,
                        raw_date="",
                        tags=[],
                        reviews=0,
                        source="Toolify",
                    )
                )
                if len(results) >= limit:
                    break
            return results[:limit]

        self._with_retry(_scrape, "Toolify section")
        return results[:limit]

    def scrape_toolify_just_launched(self, limit: int = 20) -> List[ProductItem]:
        items = self._scrape_toolify_sitemap(limit=limit)
        if items:
            return items[:limit]
        url = "https://www.toolify.ai/zh/new"
        keywords = ["刚刚推出", "Just Launched", "Today", "今日"]
        items = self._scrape_toolify_section(url, keywords, limit)
        fresh = [
            item
            for item in items
            if item.raw_date and self._is_recent_item(item, max_hours=24)
        ]
        return fresh[:limit]

    def _scrape_toolify_sitemap(self, limit: int = 20) -> List[ProductItem]:
        url = "https://www.toolify.ai/sitemap.xml"
        headers = {"User-Agent": self.user_agent}
        try:
            resp = requests.get(url, headers=headers, timeout=20)
            resp.raise_for_status()
        except Exception:
            return []
        soup = BeautifulSoup(resp.text, "xml")
        urls = []
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        for loc in soup.find_all("url"):
            link = loc.findtext("loc") or ""
            lastmod = loc.findtext("lastmod") or ""
            if "/tool/" not in link and "/zh/tool/" not in link:
                continue
            published_at = None
            if lastmod:
                try:
                    published_at = datetime.fromisoformat(lastmod.replace("Z", "+00:00"))
                except Exception:
                    published_at = None
            if published_at and published_at < cutoff:
                continue
            urls.append((link, published_at, lastmod))
        results: List[ProductItem] = []
        for link, published_at, lastmod in urls[: limit * 2]:
            name = ""
            tagline = ""
            try:
                page = requests.get(link, headers=headers, timeout=15)
                if page.status_code < 400:
                    html = page.text
                    soup = BeautifulSoup(html, "html.parser")
                    title = soup.find("title")
                    if title:
                        name = title.get_text(strip=True).replace(" - Toolify", "")
                    desc = soup.find("meta", attrs={"name": "description"})
                    if desc and desc.get("content"):
                        tagline = desc["content"].strip()
            except Exception:
                pass
            if not name:
                name = link.rstrip("/").split("/")[-1].replace("-", " ")
            results.append(
                ProductItem(
                    name=name,
                    url=link,
                    tagline=tagline,
                    published_at=published_at,
                    raw_date=lastmod or "",
                    tags=[],
                    reviews=0,
                    source="Toolify",
                )
            )
            if len(results) >= limit:
                break
        return results

    def scrape_toolify_best(self, limit: int = 10) -> List[ProductItem]:
        url = "https://www.toolify.ai/zh"
        keywords = ["Most Saved", "Weekly Best", "本周最佳", "收藏最多"]
        return self._scrape_toolify_section(url, keywords, limit)

    def scrape_aibase_details(self, name: str) -> dict:
        url = "https://app.aibase.com/zh"
        params = {"q": name}
        data = {"cn_description": "", "tags": []}

        def _scrape():
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            card = soup.find("a", string=re.compile(re.escape(name), re.I))
            if not card:
                return data
            container = card.find_parent(["article", "div", "li"]) or card
            desc = ""
            p = container.find("p")
            if p:
                desc = p.get_text(strip=True)
            tag_nodes = container.select("span, a")
            tags = []
            for node in tag_nodes:
                txt = node.get_text(strip=True)
                if txt and len(txt) <= 12:
                    tags.append(txt)
            data["cn_description"] = desc
            data["tags"] = list(dict.fromkeys(tags))[:6]
            return data

        return self._with_retry(_scrape, f"AIBase detail {name}")

    def scrape_aibase_hot(self, limit: int = 10) -> List[ProductItem]:
        url = "https://app.aibase.com/zh"
        results: List[ProductItem] = []

        def _scrape():
            page = self._new_page()
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            try:
                page.wait_for_selector("main a", timeout=10000)
            except PlaywrightTimeoutError:
                pass
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1000)
            except Exception:
                pass
            main = page.locator("main")
            html = main.inner_html() if main.count() else page.content()
            skip_titles = {"english", "中文", "首页", "home", "资讯", "登录"}
            try:
                items = self._extract_links_in_main(page, ["/ai/", "/tool/", "/product/"], skip_titles)
            except Exception:
                items = []
            seen = set()
            if items:
                for item in items:
                    title = item.get("title", "")
                    href = item.get("href", "")
                    if not title or not href:
                        continue
                    if href in seen:
                        continue
                    seen.add(href)
                    results.append(
                        ProductItem(
                            name=title,
                            url=href,
                            tagline=item.get("desc", ""),
                            published_at=None,
                            raw_date="",
                            tags=[],
                            reviews=0,
                            source="AIBase",
                        )
                    )
                    if len(results) >= limit:
                        break
                if results:
                    page.close()
                    return results
            try:
                heading_items = self._extract_cards_by_heading(page, limit, skip_titles)
            except Exception:
                heading_items = []
            if heading_items:
                for item in heading_items:
                    title = item.get("title", "")
                    href = item.get("href", "")
                    if not title or not href:
                        continue
                    if href in seen:
                        continue
                    seen.add(href)
                    results.append(
                        ProductItem(
                            name=title,
                            url=href,
                            tagline=item.get("desc", ""),
                            published_at=None,
                            raw_date="",
                            tags=[],
                            reviews=0,
                            source="AIBase",
                        )
                    )
                    if len(results) >= limit:
                        break
                if results:
                    page.close()
                    return results
            fallback = self._extract_aibase_cards_from_html(html, limit)
            if fallback:
                page.close()
                return fallback
            page.close()
            return results

        return self._with_retry(_scrape, "AIBase hot")

    def scrape_aibase_latest(self, limit: int = 20) -> List[ProductItem]:
        url = "https://app.aibase.com/zh"
        results: List[ProductItem] = []

        def _scrape():
            page = self._new_page()
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            try:
                page.wait_for_selector("main a", timeout=10000)
            except PlaywrightTimeoutError:
                pass
            main = page.locator("main")
            html = main.inner_html() if main.count() else page.content()
            skip_titles = {"english", "中文", "首页", "home", "资讯", "登录"}
            try:
                items = self._extract_links_in_main(page, ["/ai/", "/tool/", "/product/"], skip_titles)
            except Exception:
                items = []
            try:
                generic_items = self._extract_cards_generic(page, limit, skip_titles)
            except Exception:
                generic_items = []
            page.close()

            seen = set()
            if items:
                for item in items:
                    title = item.get("title", "")
                    href = item.get("href", "")
                    if not title or not href:
                        continue
                    if href in seen:
                        continue
                    seen.add(href)
                    results.append(
                        ProductItem(
                            name=title,
                            url=href,
                            tagline=item.get("desc", ""),
                            published_at=None,
                            raw_date="",
                            tags=[],
                            reviews=0,
                            source="AIBase",
                        )
                    )
                    if len(results) >= limit:
                        break
                if results:
                    page.close()
                    return results
            if generic_items:
                for item in generic_items:
                    title = item.get("title", "")
                    href = item.get("href", "")
                    if not title or not href:
                        continue
                    if href in seen:
                        continue
                    seen.add(href)
                    results.append(
                        ProductItem(
                            name=title,
                            url=href,
                            tagline=item.get("desc", ""),
                            published_at=None,
                            raw_date="",
                            tags=[],
                            reviews=0,
                            source="AIBase",
                        )
                    )
                    if len(results) >= limit:
                        break
                if results:
                    page.close()
                    return results
            fallback = self._extract_aibase_cards_from_html(html, limit)
            if fallback:
                page.close()
                return fallback
            page.close()
            return results

        return self._with_retry(_scrape, "AIBase latest")

    def scrape_aibase_category(self, category_url: str, limit: int = 10) -> List[ProductItem]:
        results: List[ProductItem] = []

        def _scrape():
            page = self._new_page()
            page.goto(category_url, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            try:
                page.wait_for_selector("main a", timeout=10000)
            except PlaywrightTimeoutError:
                pass
            main = page.locator("main")
            html = main.inner_html() if main.count() else page.content()
            skip_titles = {"english", "中文", "首页", "home", "资讯", "登录"}
            try:
                items = self._extract_links_in_main(page, ["/ai/", "/tool/", "/product/"], skip_titles)
            except Exception:
                items = []
            try:
                generic_items = self._extract_cards_generic(page, limit, skip_titles)
            except Exception:
                generic_items = []
            page.close()

            seen = set()
            if items:
                for item in items:
                    title = item.get("title", "")
                    href = item.get("href", "")
                    if not title or not href:
                        continue
                    if href in seen:
                        continue
                    seen.add(href)
                    results.append(
                        ProductItem(
                            name=title,
                            url=href,
                            tagline=item.get("desc", ""),
                            published_at=None,
                            raw_date="",
                            tags=[],
                            reviews=0,
                            source="AIBase",
                        )
                    )
                    if len(results) >= limit:
                        break
                if results:
                    page.close()
                    return results
            if generic_items:
                for item in generic_items:
                    title = item.get("title", "")
                    href = item.get("href", "")
                    if not title or not href:
                        continue
                    if href in seen:
                        continue
                    seen.add(href)
                    results.append(
                        ProductItem(
                            name=title,
                            url=href,
                            tagline=item.get("desc", ""),
                            published_at=None,
                            raw_date="",
                            tags=[],
                            reviews=0,
                            source="AIBase",
                        )
                    )
                    if len(results) >= limit:
                        break
                if results:
                    page.close()
                    return results
            fallback = self._extract_aibase_cards_from_html(html, limit)
            if fallback:
                page.close()
                return fallback
            page.close()
            return results

        return self._with_retry(_scrape, f"AIBase category {category_url}")

    def _scrape_aicpb_detail(self, url: str) -> dict:
        detail = {"description": "", "tags": []}
        if not url:
            return detail
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            meta = soup.find("meta", attrs={"name": "description"})
            if meta and meta.get("content"):
                detail["description"] = meta["content"].strip()
                return detail
            h1 = soup.find("h1")
            if h1:
                para = h1.find_next("p")
                if para:
                    detail["description"] = para.get_text(strip=True)
            return detail
        except Exception:
            return detail

    def scrape_aicpb_rankings(self) -> list[dict]:
        url = "https://www.aicpb.com/ai-rankings/products/global-ai-rankings"

        def _scrape():
            rows = []
            page = self._new_page()
            try:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_selector("tbody tr", timeout=15000)
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(1000)
                except Exception:
                    pass
                try:
                    rows_data = page.evaluate(
                        """
                        () => {
                            const rows = Array.from(document.querySelectorAll('table tbody tr'));
                            return rows.map(row => {
                                const cells = Array.from(row.querySelectorAll('td,th')).map(c => c.innerText.trim());
                                const a = row.querySelector('a[href]');
                                const href = a ? a.href : '';
                                return { cells, href };
                            });
                        }
                        """
                    )
                except Exception:
                    rows_data = []
                html = page.content()
            except Exception:
                rows_data = []
                html = ""
            finally:
                page.close()
            if rows_data:
                for row in rows_data[:20]:
                    cells = row.get("cells", [])
                    if len(cells) < 2:
                        continue
                    link = row.get("href", "")
                    rank = cells[0]
                    name = cells[1]
                    traffic = cells[2] if len(cells) > 2 else ""
                    desc = cells[3] if len(cells) > 3 else ""
                    if not rank.isdigit() or not name:
                        continue
                    rows.append(
                        {
                            "rank": rank,
                            "name": name,
                            "traffic": traffic,
                            "url": link,
                            "description": desc,
                            "tags": [],
                        }
                    )
            return rows

        try:
            return self._with_retry(_scrape, "AICPB rankings")[:20]
        except Exception:
            return []

    def calculate_quality_score(self, name: str, tagline: str = "") -> int:
        score = 0
        text = f"{name} {tagline}".lower()
        generic_phrases = [
            "text to video generator",
            "ai writer free",
            "ai writer",
            "text to video",
            "video generator",
            "image generator",
            "ai chatbot",
            "ai tool",
        ]
        if any(phrase in text for phrase in generic_phrases):
            score -= 100
        return score

    def scrape_aicpb_top(self, limit: int = 20) -> list[dict]:
        rows = self.scrape_aicpb_rankings()
        return rows[:limit]

