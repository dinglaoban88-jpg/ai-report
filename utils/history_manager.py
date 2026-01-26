"""
History Manager - 永久去重系统
用于记录已推荐过的产品，避免重复推荐
"""

import json
import os
from datetime import datetime
from typing import List, Set, Optional
import logging


class HistoryManager:
    """管理产品推荐历史，实现永久去重"""
    
    def __init__(self, history_file: str = "data/history.json"):
        """
        初始化 HistoryManager
        
        Args:
            history_file: 历史记录文件路径
        """
        self.history_file = history_file
        self._ensure_data_dir()
        self._history: List[dict] = []
        self._name_set: Set[str] = set()
        self._url_set: Set[str] = set()
        self._load()
    
    def _ensure_data_dir(self):
        """确保 data 目录存在"""
        data_dir = os.path.dirname(self.history_file)
        if data_dir and not os.path.exists(data_dir):
            os.makedirs(data_dir, exist_ok=True)
    
    def _load(self):
        """从文件加载历史记录"""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    self._history = json.load(f)
                # 构建快速查找集合
                for item in self._history:
                    name = self._normalize(item.get("name", ""))
                    url = self._normalize_url(item.get("url", ""))
                    if name:
                        self._name_set.add(name)
                    if url:
                        self._url_set.add(url)
                logging.info(f"Loaded {len(self._history)} items from history")
            except (json.JSONDecodeError, IOError) as e:
                logging.warning(f"Failed to load history: {e}")
                self._history = []
        else:
            self._history = []
            logging.info("No existing history file, starting fresh")
    
    def _normalize(self, name: str) -> str:
        """标准化产品名（小写，去除空白）"""
        return name.lower().strip()
    
    def _normalize_url(self, url: str) -> str:
        """标准化 URL（小写，去除查询参数和尾部斜杠）"""
        url = url.lower().strip()
        # 去除查询参数
        url = url.split("?")[0]
        # 去除尾部斜杠
        url = url.rstrip("/")
        return url
    
    def is_duplicate(self, name: str, url: str = "") -> bool:
        """
        检查产品是否已经推荐过
        
        Args:
            name: 产品名称
            url: 产品 URL（可选）
        
        Returns:
            True 如果是重复产品
        """
        normalized_name = self._normalize(name)
        normalized_url = self._normalize_url(url) if url else ""
        
        # 检查名称匹配
        if normalized_name and normalized_name in self._name_set:
            return True
        
        # 检查 URL 匹配
        if normalized_url and normalized_url in self._url_set:
            return True
        
        return False
    
    def add(self, name: str, url: str, source: str = "", date: Optional[str] = None):
        """
        添加产品到历史记录
        
        Args:
            name: 产品名称
            url: 产品 URL
            source: 来源（如 Product Hunt, GitHub）
            date: 推荐日期（默认为今天）
        """
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")
        
        normalized_name = self._normalize(name)
        normalized_url = self._normalize_url(url)
        
        # 避免重复添加
        if self.is_duplicate(name, url):
            return
        
        item = {
            "name": name,
            "url": url,
            "source": source,
            "date": date,
        }
        self._history.append(item)
        
        if normalized_name:
            self._name_set.add(normalized_name)
        if normalized_url:
            self._url_set.add(normalized_url)
    
    def save(self):
        """保存历史记录到文件"""
        self._ensure_data_dir()
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self._history, f, ensure_ascii=False, indent=2)
            logging.info(f"Saved {len(self._history)} items to history")
        except IOError as e:
            logging.error(f"Failed to save history: {e}")
    
    def save_recommendations(self, recommendations: List[dict]):
        """
        批量保存推荐的产品
        
        Args:
            recommendations: 推荐产品列表，每个包含 name, url, source
        """
        for item in recommendations:
            self.add(
                name=item.get("name", ""),
                url=item.get("url", ""),
                source=item.get("source", ""),
            )
        self.save()
    
    def get_stats(self) -> dict:
        """获取历史统计信息"""
        sources = {}
        for item in self._history:
            source = item.get("source", "Unknown")
            sources[source] = sources.get(source, 0) + 1
        
        return {
            "total": len(self._history),
            "by_source": sources,
        }
    
    def clear(self):
        """清空历史记录（谨慎使用）"""
        self._history = []
        self._name_set = set()
        self._url_set = set()
        self.save()
