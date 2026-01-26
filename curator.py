from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import List, Tuple

from cleaner import deduplicate, select_top
from fetchers import (
    fetch_futurepedia,
    fetch_github_ai,
    fetch_hacker_news_ai,
    fetch_product_hunt_rss,
    fetch_taaft_timeline,
    fetch_toolify_sitemap,
)
from llm_client import LLMClient
from scraper import ProductItem, Scraper


class Curator:
    def __init__(self, scraper: Scraper, llm: LLMClient, store_path: str, history=None) -> None:
        self.scraper = scraper
        self.llm = llm
        self.store_path = store_path
        self.history = history  # HistoryManager 实例，用于永久去重
        # 领域黑名单：包含这些词的产品直接剔除
        # 【原则】只服务于「搞钱、搞创作、搞效率」场景
        self.block_keywords = {
            # 【开发者工具 - 必杀词】
            "sdk", "api", "cli", "boilerplate", "template", "starter kit", "starter-kit",
            "library", "framework", "database", "backend", "frontend", "deploy",
            "kubernetes", "k8s", "docker", "container", "serverless", "lambda",
            "agent core", "agentcore", "open source", "open-source", "self-hosted",
            "devops", "devtool", "developer tool", "infrastructure", "terraform",
            "npm", "pip", "cargo", "maven", "gradle", "package manager",
            "python library", "node module", "react component", "vue component",
            "microservice", "orchestration", "ci/cd", "pipeline",
            "mcp", "server", "python", "fastmcp", "client", "repo",
            # 【社交/社区 - 杀无赦】消耗时间的平台
            "network", "community", "social", "connect", "dating", "meet",
            "club", "forum", "social media", "social network", "art network",
            # 【家居/生活 - 杀无赦】
            "home design", "interior", "decor", "furniture", "reimagine home",
            "wallpaper", "room design", "house", "garden", "kitchen design",
            "smart home", "home automation", "iot", "appliance",
            # 【习惯/日记/情感 - 杀无赦】
            "habit", "habitz", "habit tracker", "goal tracker", "streak",
            "journaling", "diary", "mood tracker", "mood",
            "gratitude", "reflection", "self-care", "mindfulness",
            "daily routine", "morning routine", "routine",
            # 【简历/低质量工具】
            "resume", "cv builder", "resume builder",
            # 生活/育儿/健康类
            "baby", "parenting", "infant", "toddler", "pregnancy", "mother", "父母",
            "health", "fitness", "workout", "exercise", "sleep", "meditation", "wellness",
            "caffeine", "calorie", "diet", "weight", "nutrition", "yoga", "breathing",
            # 约会类
            "girlfriend", "boyfriend", "romance", "love", "match", "relationship",
            "nsfw", "adult",
            # 游戏/娱乐类
            "game", "gaming", "puzzle", "arcade", "casino", "trivia", "quiz game",
            "tarot", "horoscope", "astrology", "fortune", "zodiac",
            # 购物/时尚类
            "shopping", "fashion", "clothing", "beauty", "cosmetic", "skincare",
            "crypto", "bitcoin", "trading", "stock", "forex", "nft",
            # 其他生活类
            "face swap", "protocol",
            "k12", "k-12", "tutor", "tutoring", "flashcard", "study",
            "food", "recipe", "cooking", "restaurant", "meal", "grocery",
            "pet", "dog", "cat", "travel", "vacation", "hotel", "flight",
            "weather", "calendar", "reminder", "alarm", "timer",
        }
        # 通用名黑名单：这些名称太通用，缺乏品牌辨识度
        self.generic_names = {
            "translator", "3d viewer", "ai chat", "chatbot", "assistant",
            "converter", "downloader", "editor", "generator", "maker",
            "ai writer", "text to video", "video generator", "image generator",
        }
        self.aicpb_block_list = {
            "chatgpt", "claude", "gemini", "copilot",
            "dall", "openai", "quillbot",
        }
        self.allowed_sources = {"Toolify", "Product Hunt", "AIBase", "AICPB", "GitHub", "Hacker News", "TAAFT", "Futurepedia"}
        
        # 【效率明星白名单】这些巨头产品如果发布 AI 新功能，允许推荐
        self.efficiency_stars = {
            "notion", "raycast", "obsidian", "canva", "figma", "miro",
            "wps", "feishu", "飞书", "arc", "arc browser",
            "perplexity", "genspark", "anygen", "linear",
            "airtable", "coda", "clickup", "monday", "asana",
            "midjourney", "runway", "pika", "luma", "kling",
        }
        
        # 【开发者工具黑名单】面向开发者的工具，一律拦截
        # 注意：添加空格前缀避免子字符串误匹配（如 "ide" 匹配 "video"）
        self.devtools_blocklist = {
            # 部署/运维类 - 绝杀
            "deploy", "deployment", "backend", "devops", "infrastructure",
            " server", "hosting", "kubernetes", "docker", "container",
            "ci/cd", "monitoring", "observability",
            "serverless", " runtime", "capsule", "capsules",
            # SDK/API类 - 绝杀
            " sdk", " api ", "webhook", "endpoint", "rest api", "graphql",
            " mcp", "fastmcp",  # Model Context Protocol
            # IDE/编程类 - 绝杀（避免匹配 video/slide）
            " ide ", "code editor", "debugger", "compiler", " terminal",
            # 数据库类 - 绝杀
            " database", " sql", "nosql", "postgres", "mongodb", "redis",
            # 开源基建类 - 绝杀
            "open source", "open-source", "github repo", "npm package",
            # 硬件/固件类 - 绝杀
            "firmware", " hardware", "embedded", " iot", "raspberry",
            # Agent/Builder 类 - 绝杀（造 App 的，不是用 App 的）
            "agent builder", "agent platform", "agent framework",
            "app builder", "workflow builder", "automation builder",
            "low-code platform", "no-code platform",
        }
        
        # 【基建厂商黑名单】这些公司卖的是基建，不是成品 SaaS
        self.vendor_blocklist = {
            "netlify", "vercel", "aws", "amazon web services",
            "azure", "google cloud", "gcp", "cloudflare",
            "supabase", "docker", "kubernetes", "gitlab",
            "heroku", "digitalocean", "linode", "fly.io", "railway",
            "render", "planetscale", "neon", "upstash",
        }
        
        # 【虚拟伴侣/二次元黑名单】PM 不关心电子宠物、虚拟女友
        self.companion_blocklist = {
            "companion", "waifu", "live2d", "virtual friend",
            "girlfriend", "boyfriend", "dating", "roleplay",
            "anime", "character ai", "soulmate", "vtuber",
            "virtual pet", "ai friend", "ai girlfriend", "ai boyfriend",
            "emotional support", "chat companion", "ai companion",
            "facetime", "video call",  # 除非明确是会议工具
        }
        
        # 【垂直行业黑名单】PM 关注通用工具，不要太垂直的行业
        self.vertical_blocklist = {
            # 金融/交易
            "trading", "trader", "crypto", "bitcoin", "stock", "forex",
            "investment", "portfolio", "hedge fund", "quantitative",
            # 医疗/健康
            "medical", "diagnosis", "healthcare", "clinical", "patient",
            "hospital", "doctor", "pharmacy", "drug", "symptom",
            # 法律（除非是通用合同）
            "legal advice", "lawyer", "attorney", "litigation", "lawsuit",
            # 房地产
            "real estate", "property", "mortgage", "rental",
            # 其他垂直
            "insurance", "agriculture", "farming", "mining",
        }
        
        # 【基础设施黑名单】Model/Cloud 提供商
        self.infra_blocklist = {
            "chatgpt", "claude", "gemini", "gpt-4", "gpt-5",
            "openai", "anthropic", "google ai", "meta ai",
            "azure", "aws", "gcp", "lambda",
        }

    def _load_history(self) -> list[dict]:
        if not os.path.exists(self.store_path):
            return []
        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception as exc:  # noqa: BLE001
            logging.warning("Failed to load history: %s", exc)
        return []

    def _save_history(self, items: list[dict]) -> None:
        os.makedirs(os.path.dirname(self.store_path), exist_ok=True)
        with open(self.store_path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)

    def _recent_seen(self, days: int = 30) -> set[str]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        seen = set()
        for item in self._load_history():
            ts = item.get("ts")
            try:
                dt = datetime.fromisoformat(ts)
            except Exception:
                continue
            if dt >= cutoff:
                key = (item.get("url") or item.get("name") or "").strip().lower()
                if key:
                    seen.add(key)
        return seen

    def _append_history(self, selections: list[dict]) -> None:
        history = self._load_history()
        now = datetime.now(timezone.utc).isoformat()
        for item in selections:
            key = (item.get("url") or item.get("name") or "").strip()
            if not key:
                continue
            history.append(
                {
                    "name": item.get("name", ""),
                    "url": item.get("url", ""),
                    "ts": now,
                }
            )
        self._save_history(history)

    def _clean_description(self, name: str, text: str) -> str:
        cleaned = (text or "").strip()
        if not cleaned:
            return ""
        if name:
            dup = f"{name} - {name}"
            cleaned = cleaned.replace(dup, name)
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        return cleaned.strip()

    def _clean_for_llm(self, text: str) -> str:
        """预清洗文本，移除元数据噪音，给 LLM 更纯净的输入"""
        if not text:
            return ""
        cleaned = text
        # 移除时间戳噪音
        cleaned = re.sub(r'\d+\s*(days?|hours?|minutes?|mins?|hrs?)\s*ago', '', cleaned, flags=re.I)
        # 移除元数据标签
        cleaned = re.sub(r'(Discussion|Comments?|Link|Source:|Updated:|Created:)[^\n]*', '', cleaned, flags=re.I)
        # 移除 Markdown 链接语法
        cleaned = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', cleaned)
        # 移除 GitHub 徽章/badge 代码
        cleaned = re.sub(r'!\[.*?\]\(.*?\)', '', cleaned)
        cleaned = re.sub(r'<img[^>]*>', '', cleaned, flags=re.I)
        # 移除 star/fork 计数
        cleaned = re.sub(r'[⭐★☆]\s*\d+[kK]?', '', cleaned)
        cleaned = re.sub(r'\b\d+\s*(stars?|forks?|watchers?)\b', '', cleaned, flags=re.I)
        # 移除 HTML 标签
        cleaned = re.sub(r'<[^>]+>', '', cleaned)
        # 移除多余空白
        cleaned = re.sub(r'\s+', ' ', cleaned)
        cleaned = re.sub(r'\s*[|\-–—]\s*$', '', cleaned)
        return cleaned.strip()

    def _to_candidate(self, item: ProductItem) -> dict:
        desc = self._clean_description(item.name, item.tagline)
        text = f"{item.name} {desc} {' '.join(item.tags)}".strip()
        words = text.split()
        return {
            "name": item.name,
            "url": item.url,
            "tagline": desc,
            "description": desc,
            "context": text,
            "low_quality": len(words) < 10,
            "tags": item.tags,
            "source": item.source,
        }

    def _to_candidate_dict(self, item: dict) -> dict:
        desc = self._clean_description(item.get("name", ""), item.get("tagline", ""))
        # 预清洗，移除元数据噪音
        desc = self._clean_for_llm(desc)
        text = f"{item.get('name','')} {desc}".strip()
        words = text.split()
        return {
            "name": item.get("name", ""),
            "url": item.get("url", ""),
            "tagline": desc,
            "description": desc,
            "context": text,
            "low_quality": len(words) < 10,
            "tags": item.get("tags", []),
            "source": item.get("source", ""),
        }

    def enrich_with_search(self, name: str, is_github: bool = False) -> str:
        """使用 Tavily 深度搜索获取产品信息（含产地侦探）"""
        if not name:
            return ""
        
        # 根据产品类型构建搜索查询（包含产地信息）
        if is_github:
            query = f"{name} GitHub readme features company headquarters founders location"
        else:
            query = f"{name} AI tool features review company headquarters founders Chinese"
        
        # 尝试使用 Tavily
        try:
            from tavily import TavilyClient
            
            # 从配置读取 API Key
            tavily_key = self._get_tavily_key()
            if not tavily_key:
                return self._fallback_search(name)
            
            client = TavilyClient(api_key=tavily_key)
            response = client.search(
                query=query,
                search_depth="advanced",
                include_answer=True,
                max_results=3,
            )
            
            # 优先使用 Tavily 的 AI 生成答案
            answer = response.get("answer", "")
            if answer and len(answer) > 50:
                return self._clean_for_llm(answer)
            
            # 如果没有 answer，拼接 results
            results = response.get("results", [])
            summaries = []
            for r in results[:3]:
                content = r.get("content", "").strip()
                if content and len(content) > 20:
                    summaries.append(content)
            
            if summaries:
                return self._clean_for_llm(" ".join(summaries)[:800])
            
        except Exception as exc:
            logging.warning("Tavily search failed for %s: %s", name, exc)
        
        # 降级：抓取产品主页 Meta Description
        return self._fallback_search(name)
    
    def _get_tavily_key(self) -> str:
        """获取 Tavily API Key"""
        # 优先从环境变量读取
        import os
        key = os.getenv("TAVILY_API_KEY", "")
        if key:
            return key
        # 从 config.json 读取
        try:
            config_path = os.path.join(os.path.dirname(__file__), "config.json")
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg.get("tavily_api_key", "")
        except Exception:
            return ""
    
    def _fallback_search(self, name: str) -> str:
        """降级方案：抓取产品主页获取 Meta Description"""
        try:
            import requests
            from bs4 import BeautifulSoup
            
            # 搜索产品主页
            search_url = f"https://www.google.com/search?q={name}+official+site"
            headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
            # 直接尝试常见域名
            possible_urls = [
                f"https://{name.lower().replace(' ', '')}.com",
                f"https://{name.lower().replace(' ', '')}.ai",
                f"https://www.{name.lower().replace(' ', '')}.com",
            ]
            
            for url in possible_urls[:1]:  # 只尝试第一个
                try:
                    resp = requests.get(url, headers=headers, timeout=10)
                    if resp.status_code == 200:
                        soup = BeautifulSoup(resp.text, "html.parser")
                        # 获取 meta description
                        meta = soup.find("meta", attrs={"name": "description"})
                        if meta and meta.get("content"):
                            return self._clean_for_llm(meta["content"])
                except Exception:
                    continue
        except Exception as exc:
            logging.debug("Fallback search failed for %s: %s", name, exc)
        
        return ""

    def _is_giant(self, name: str, description: str = "") -> bool:
        """判断是否应该拦截（开发者工具/基建厂商/虚拟伴侣拦截，效率明星放行）"""
        haystack = f"{name} {description}".lower()
        
        # 【效率明星白名单】这些产品如果有 AI 新功能，允许推荐
        if any(star in haystack for star in self.efficiency_stars):
            return False  # 放行
        
        # 【虚拟伴侣/二次元黑名单】电子宠物、虚拟女友一律拦截
        if any(companion in haystack for companion in self.companion_blocklist):
            return True  # 拦截
        
        # 【垂直行业黑名单】太垂直的行业工具一律拦截
        if any(vertical in haystack for vertical in self.vertical_blocklist):
            return True  # 拦截
        
        # 【基建厂商黑名单】这些公司卖基建，不是成品 SaaS
        if any(vendor in haystack for vendor in self.vendor_blocklist):
            return True  # 拦截
        
        # 【开发者工具黑名单】面向开发者的工具一律拦截
        if any(dev in haystack for dev in self.devtools_blocklist):
            return True  # 拦截
        
        # 【基础设施黑名单】Model/Cloud 提供商一律拦截
        if any(infra in haystack for infra in self.infra_blocklist):
            return True  # 拦截
        
        return False

    def _is_generic_name(self, name: str) -> bool:
        """检查产品名是否过于通用，缺乏品牌辨识度"""
        lowered = name.lower().strip()
        # 完全匹配通用名
        if lowered in self.generic_names:
            return True
        # 检查是否是单个通用词
        for generic in self.generic_names:
            if lowered == generic or f" {generic}" in f" {lowered} ":
                return True
        # 检查是否只有通用词组成（如 "AI Chat Bot"）
        words = lowered.split()
        generic_words = {"ai", "tool", "app", "bot", "assistant", "helper", "generator", "maker", "viewer", "editor"}
        if len(words) <= 3 and all(w in generic_words for w in words):
            return True
        return False

    def _prefilter(self, candidates: List[dict]) -> List[dict]:
        filtered = []
        for c in candidates:
            haystack = f"{c.get('name','')} {c.get('tagline','')} {c.get('description','')}".lower()
            if any(k in haystack for k in self.block_keywords):
                continue
            desc = f"{c.get('tagline', '')} {c.get('description', '')}"
            if self._is_giant(c.get("name", ""), desc):
                continue
            if self._is_generic_name(c.get("name", "")):
                continue
            score = self.scraper.calculate_quality_score(
                c.get("name", ""), c.get("tagline", "")
            )
            if score <= -50:
                continue
            filtered.append(c)
        return filtered

    def _is_dev_tool(self, name: str, tagline: str, description: str) -> bool:
        """检查是否为开发者工具（PM不关心，给开发者用的一律拦截）"""
        haystack = f"{name} {tagline} {description}".lower()
        
        # 【必杀词】看到就杀，无需辩护
        dev_killers = {
            # 基建/运维
            "deploy", "deployment", "backend", "devops", "infrastructure",
            "serverless", "hosting", "ci/cd", "cicd",
            "kubernetes", "k8s", "docker", "container", "terraform",
            "aws ", "azure ", "gcp ", "lambda", "monitoring", "observability",
            # 代码/开发
            " sdk", " api", " cli ", "boilerplate", "starter kit",
            " library", " framework", " database", " npm", "pip install",
            "python library", "node module", "open source", "open-source",
            " git ", "github", "gitlab", "repository", "debugger",
            " terminal", " shell ", "code editor",
            # Agent/Builder 平台（造App的工具）
            "agent builder", "agent platform", "agent framework", "agent core",
            "app builder", "code generator", "code generation", " mcp",
            "low-code platform", "no-code platform", "developer tool",
        }
        for killer in dev_killers:
            if killer in haystack:
                return True
        
        # 【高危词】需要结合上下文判断
        high_risk = {"builder", "no-code", "low-code"}
        # 如果包含高危词，且没有明确的最终用户场景，也拦截
        if any(risk in haystack for risk in high_risk):
            # 检查是否有明确的用户场景（文档/设计/协作）
            user_scenarios = {"document", "design", "presentation", "meeting", 
                              "writing", "note", "collaboration", "team", "video", "image"}
            if not any(scene in haystack for scene in user_scenarios):
                return True
        
        # GitHub 项目默认视为开发工具（除非有明确的 App/GUI 场景）
        if "github.com" in haystack:
            app_indicators = {"app", "gui", "desktop", "web app", "chrome extension"}
            if not any(ind in haystack for ind in app_indicators):
                return True
        
        return False

    def _prefilter_value(self, candidates: List[dict]) -> List[dict]:
        filtered = []
        for c in candidates:
            name = c.get("name", "")
            url = c.get("url", "")
            
            # 【永久去重】检查是否已经推荐过
            if self.history and self.history.is_duplicate(name, url):
                logging.debug("Filtered duplicate: %s", name)
                continue
            
            haystack = f"{name} {c.get('tagline','')} {c.get('description','')}".lower()
            if any(k in haystack for k in self.block_keywords):
                continue
            desc = f"{c.get('tagline', '')} {c.get('description', '')}"
            if self._is_giant(name, desc):
                continue
            if self._is_generic_name(name):
                continue
            # 【新增】开发者工具过滤
            if self._is_dev_tool(name, c.get("tagline", ""), c.get("description", "")):
                logging.debug("Filtered dev tool: %s", name)
                continue
            score = self.scraper.calculate_quality_score(
                name, c.get("tagline", "")
            )
            if score <= -50:
                continue
            filtered.append(c)
        return filtered

    def _select_from_source(self, candidates: List[dict], source_name: str) -> dict:
        seen = self._recent_seen(days=30)
        remaining = self._prefilter(candidates[:])
        for _ in range(2):
            try:
                choice = self.llm.select_best(remaining, source_name)
            except Exception:
                choice = None
            if not choice:
                break
            name = (choice.get("name") or "").strip()
            if name and name.lower() not in seen:
                self._append_seen(name)
                return choice
            remaining = [c for c in remaining if c.get("name") != name]
        for candidate in remaining:
            name = (candidate.get("name") or "").strip()
            if name and name.lower() not in seen:
                self._append_seen(name)
                return {
                    "name": candidate.get("name", ""),
                    "url": candidate.get("url", ""),
                    "one_sentence_intro_cn": candidate.get("tagline")
                    or candidate.get("description")
                    or "用于办公或内容创作的实用工具。",
                }
        if candidates:
            return {
                "name": candidates[0].get("name", ""),
                "url": candidates[0].get("url", ""),
                "one_sentence_intro_cn": candidates[0].get("tagline")
                or candidates[0].get("description")
                or "用于办公或内容创作的实用工具。",
            }
        return {"name": "", "url": "", "one_sentence_intro_cn": "用于办公或内容创作的实用工具。"}

    def get_today_news(self) -> List[dict]:
        """获取今日新品（Part 1）- 应用同样的黑名单过滤"""
        now = datetime.now(timezone.utc)
        start_of_day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        products: List[dict] = []
        sources = [
            fetch_product_hunt_rss(),
            fetch_toolify_sitemap(),
            fetch_hacker_news_ai(),
            fetch_github_ai(),
            fetch_taaft_timeline(),
        ]
        for items in sources:
            for item in items:
                published_at = item.get("published_at")
                if not published_at:
                    continue
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=timezone.utc)
                if published_at < start_of_day or published_at > now:
                    continue
                # 【Part 1 黑名单过滤】
                tagline = item.get('tagline', '')
                haystack = f"{item.get('name','')} {tagline} {item.get('url','')}".lower()
                if not item.get("url") or self._is_giant(item.get("name", ""), tagline):
                    continue
                if any(k in haystack for k in self.block_keywords):
                    continue
                # 【开发者工具过滤】
                if any(dev in haystack for dev in self.devtools_blocklist):
                    continue
                # 【基建厂商过滤】
                if any(vendor in haystack for vendor in self.vendor_blocklist):
                    continue
                products.append(item)
        products = deduplicate(products)
        products = select_top(products, limit=30)
        return products

    def get_new_products(self) -> List[dict]:
        return self.get_today_news()

    def get_weekly_gems(self) -> List[dict]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        # Toolify 的时间窗口放宽到 30 天（因为 sitemap 日期可能不准）
        toolify_cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        candidates: List[dict] = []
        sources = [
            fetch_product_hunt_rss(limit=30),
            fetch_toolify_sitemap(limit=50),  # 增加 Toolify 数量
            fetch_hacker_news_ai(limit=30),
            fetch_github_ai(limit=30),
            fetch_taaft_timeline(limit=30),
            fetch_futurepedia(limit=30),  # 新增 Futurepedia（应用层工具）
        ]
        for items in sources:
            for item in items:
                source = item.get("source", "")
                published_at = item.get("published_at")
                if published_at and published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=timezone.utc)
                
                # Toolify 特殊处理：sitemap 日期完全不可靠，不过滤日期
                if source == "Toolify":
                    pass  # 接受所有 Toolify 数据
                else:
                    # 其他来源：7 天内
                    if published_at and published_at < cutoff:
                        continue
                
                tagline = item.get("tagline", "") or item.get("description", "")
                if self._is_giant(item.get("name", ""), tagline):
                    continue
                if source == "GitHub":
                    stars = item.get("stars", 0)
                    homepage = item.get("homepage") or ""
                    if not homepage and stars < 200:
                        continue
                candidates.append(item)
        candidates = deduplicate(candidates)
        part1_keys = {
            (item.get("url") or item.get("name") or "").strip().lower()
            for item in self.get_today_news()
        }
        candidates = [
            c
            for c in candidates
            if (c.get("url") or c.get("name") or "").strip().lower() not in part1_keys
        ]
        return candidates

    def _fallback_reason(self, name: str, desc: str) -> str:
        """生成备用推荐语（当 LLM 失败时使用）"""
        # 清洗描述
        clean_desc = self._clean_for_llm(desc) if desc else ""
        
        # 提取 Tavily 答案（如果有）
        tavily_answer = ""
        if "| Tavily:" in clean_desc:
            parts = clean_desc.split("| Tavily:")
            clean_desc = parts[0].strip()
            if len(parts) > 1:
                tavily_answer = parts[1].strip()
        
        # 如果描述是中文，直接使用
        if clean_desc and any('\u4e00' <= c <= '\u9fa5' for c in clean_desc):
            return clean_desc[:100]
        
        # 尝试用 LLM 单独翻译这个产品
        try:
            source_text = tavily_answer[:200] if tavily_answer else clean_desc[:200]
            if source_text:
                translated = self.llm.one_line_summary(name, source_text)
                if translated and not translated.startswith("(待翻译)"):
                    return translated
        except Exception:
            pass
        
        # 最终 fallback - 根据产品类型生成简洁描述
        name_lower = name.lower()
        if any(k in name_lower for k in ["doc", "note", "pdf", "知识", "文档"]):
            return f"{name} 是一款智能文档处理工具，可提升知识管理效率。"
        elif any(k in name_lower for k in ["video", "image", "art", "design", "视频", "图片"]):
            return f"{name} 是一款 AI 创作工具，可快速生成专业视觉内容。"
        elif any(k in name_lower for k in ["meet", "team", "project", "会议", "协作"]):
            return f"{name} 是一款办公协作工具，可提升团队工作效率。"
        elif any(k in name_lower for k in ["write", "copy", "text", "写作", "文案"]):
            return f"{name} 是一款 AI 写作助手，可快速生成高质量文案。"
        else:
            return f"{name} 是一款专注于办公效率的 AI 工具。"

    def curate(self) -> List[dict]:
        recent_seen = self._recent_seen(days=30)

        def _dedupe(items: List[dict], apply_recent: bool = True) -> List[dict]:
            seen = set()
            output = []
            for item in items:
                key = (item.get("url") or item.get("name") or "").strip().lower()
                if not key or key in seen or (apply_recent and key in recent_seen):
                    continue
                seen.add(key)
                output.append(item)
            return output

        source_candidates: dict[str, List[dict]] = {
            "Product Hunt": [],
            "Toolify": [],
            "GitHub": [],
            "Hacker News": [],
            "TAAFT": [],
        }
        weekly = self.get_weekly_gems()
        for item in weekly:
            source = item.get("source") or ""
            candidate = self._to_candidate_dict(item)
            if source in source_candidates:
                source_candidates[source].append(candidate)

        def _pick_one(source: str) -> tuple[Optional[dict], list[dict]]:
            items = self._prefilter_value(source_candidates.get(source, [])[:])
            if not items:
                return None, []
            try:
                best = self.llm.select_best(items, source)
            except Exception:
                best = None
            if best and best.get("name"):
                best_key = (best.get("url") or best.get("name") or "").strip().lower()
                remaining = [
                    i
                    for i in items
                    if (i.get("url") or i.get("name", "")).strip().lower() != best_key
                ]
                return (
                    {
                        "name": best.get("name", ""),
                        "url": best.get("url", ""),
                        "one_sentence_intro_cn": best.get("one_sentence_intro_cn", ""),
                        "source": source,
                    },
                    remaining,
                )
            fallback = items[0]
            remaining = items[1:]
            return (
                {
                    "name": fallback.get("name", ""),
                    "url": fallback.get("url", ""),
                    "one_sentence_intro_cn": self._fallback_reason(
                        fallback.get("name", ""),
                        fallback.get("description", ""),
                    ),
                    "source": source,
                },
                remaining,
            )

        selections: list[dict] = []

        # 【简化流程】直接将候选转换并过滤
        remaining_pool = [
            self._to_candidate_dict(item)
            for item in weekly
        ]
        remaining_pool = self._prefilter_value(remaining_pool)
        
        # 【数据源拼盘】按来源分组，确保多样性
        source_pools = {
            "Product Hunt": [],
            "Toolify": [],
            "GitHub": [],
            "Hacker News": [],
            "TAAFT": [],
            "Futurepedia": [],
            "Other": [],
        }
        for item in remaining_pool:
            source = item.get("source", "Other")
            if source in source_pools:
                source_pools[source].append(item)
            else:
                source_pools["Other"].append(item)
        
        # 【强制保送策略 - Force Slot Allocation】
        # 确保来源多样性，防止单一来源霸占
        # Slot 1: Product Hunt (非开发类 Top 1)
        # Slot 2: Toolify (强制保送 - 必须有)
        # Slot 3: TAAFT / Futurepedia (应用层)
        # Slot 4: 综合最高分
        balanced_candidates = []
        forced_sources = set()  # 记录已强制占位的来源
        
        # 【Slot 1】Product Hunt - 先取一个
        if source_pools["Product Hunt"]:
            balanced_candidates.append(source_pools["Product Hunt"][0])
            forced_sources.add("Product Hunt")
        
        # 【Slot 2】Toolify - 强制保送（最重要的多样性保证）
        if source_pools["Toolify"]:
            balanced_candidates.append(source_pools["Toolify"][0])
            forced_sources.add("Toolify")
        
        # 【Slot 3】TAAFT/Futurepedia - 应用层工具
        taaft_futurepedia = source_pools["TAAFT"] + source_pools["Futurepedia"]
        if taaft_futurepedia:
            balanced_candidates.append(taaft_futurepedia[0])
            forced_sources.add("TAAFT")
        
        # 【Slot 4】从 Hacker News 补充一个（Show HN 产品发布）
        if source_pools["Hacker News"]:
            balanced_candidates.append(source_pools["Hacker News"][0])
            forced_sources.add("Hacker News")
        
        # 【Slot 5】从 GitHub 补充一个（有 homepage 的项目）
        if source_pools["GitHub"]:
            balanced_candidates.append(source_pools["GitHub"][0])
            forced_sources.add("GitHub")
        
        # 【Slot 6+】轮询补充，确保多样性
        source_order = ["TAAFT", "Product Hunt", "Toolify", "Hacker News"]
        for source in source_order:
            for item in source_pools[source]:
                if item not in balanced_candidates:
                    balanced_candidates.append(item)
                    break  # 每个来源只取一个
            if len(balanced_candidates) >= 8:
                break
        
        # 只取前 6 个候选（减少 Tavily 调用次数）
        candidates_for_llm = balanced_candidates[:6]
        
        logging.info("Balanced candidates: PH=%d, Toolify=%d, TAAFT=%d, GH/HN=%d",
                     len([c for c in candidates_for_llm if c.get("source") == "Product Hunt"]),
                     len([c for c in candidates_for_llm if c.get("source") == "Toolify"]),
                     len([c for c in candidates_for_llm if c.get("source") == "TAAFT"]),
                     len([c for c in candidates_for_llm if c.get("source") in ("GitHub", "Hacker News")]))
        
        # 【Tavily 深度搜索增强】为候选产品获取详细信息
        for candidate in candidates_for_llm:
            name = candidate.get("name", "")
            if not name:
                continue
            # 判断是否为 GitHub 项目
            is_github = candidate.get("source") == "GitHub"
            # 获取搜索摘要
            search_summary = self.enrich_with_search(name, is_github=is_github)
            if search_summary:
                # 将搜索结果附加到描述中
                original_desc = candidate.get("description", "") or candidate.get("tagline", "")
                candidate["description"] = f"{original_desc} | Tavily: {search_summary}"
        
        # 【核心】让 LLM 选择并翻译
        try:
            llm_picks = self.llm.select_top_n(candidates_for_llm, min_items=3, max_items=4)
        except Exception as e:
            logging.warning("LLM select_top_n failed: %s", e)
            llm_picks = []
        
        for pick in llm_picks:
            intro = pick.get("one_sentence_intro_cn", "")
            # 后处理：如果 LLM 返回的仍是英文，标记为待翻译
            if intro and not any('\u4e00' <= c <= '\u9fa5' for c in intro):
                intro = f"(待翻译) {intro[:80]}"
            
            # 【产地验证 - 无罪推定】如果标记为 CN 但没有明确证据，降级为 Global
            origin = pick.get("origin", "Global")
            if origin == "CN":
                # 在 candidates 中查找对应产品的描述
                desc = ""
                for cand in balanced_candidates:
                    if cand.get("name", "").lower() == pick.get("name", "").lower():
                        desc = f"{cand.get('tagline', '')} {cand.get('description', '')}".lower()
                        break
                # 必须有明确的中国证据
                cn_evidence = ["北京", "上海", "深圳", "杭州", "中国", "china", "beijing", "shanghai", 
                               "shenzhen", "hangzhou", "icp", "备案", ".cn", "moonshot", "智谱", "百度"]
                if not any(ev in desc for ev in cn_evidence):
                    origin = "Global"  # 证据不足，降级为海外
            
            selections.append(
                {
                    "name": pick.get("name", ""),
                    "url": pick.get("url", ""),
                    "one_sentence_intro_cn": intro,
                    "source": pick.get("source", ""),
                    "origin": origin,
                }
            )
        
        # Fallback: 如果 LLM 返回不足 3 个，用原始 tagline
        if len(selections) < 3:
            for item in balanced_candidates:
                if len(selections) >= 3:
                    break
                if any(s.get("name") == item.get("name") for s in selections):
                    continue
                tagline = item.get("tagline", "") or item.get("description", "")
                selections.append(
                    {
                        "name": item.get("name", ""),
                        "url": item.get("url", ""),
                        "one_sentence_intro_cn": self._fallback_reason(
                            item.get("name", ""),
                            tagline,
                        ),
                        "source": item.get("source", ""),
                        "origin": "Global",  # Fallback 默认海外
                    }
                )
        selections = selections[:4]

        self._append_history(selections)
        return selections
