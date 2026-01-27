from __future__ import annotations

import logging
import time
from typing import List, Optional, Union

import requests


class Notifier:
    """支持多个 Webhook 的通知器"""
    
    def __init__(self, webhook_url: Union[str, List[str]], timeout: int = 20) -> None:
        # 支持单个 URL 或 URL 列表
        if isinstance(webhook_url, str):
            self.webhook_urls = [webhook_url] if webhook_url.strip() else []
        else:
            self.webhook_urls = [url for url in webhook_url if url and url.strip()]
        self.timeout = timeout

    def send_markdown(self, markdown: str) -> bool:
        """向所有配置的 Webhook 发送消息"""
        if not self.webhook_urls:
            logging.warning("No webhook URLs configured")
            return False
        
        success_count = 0
        for url in self.webhook_urls:
            if self._send_to_webhook(url, markdown):
                success_count += 1
        
        logging.info("Webhook sent to %d/%d endpoints", success_count, len(self.webhook_urls))
        return success_count > 0
    
    def _send_to_webhook(self, url: str, markdown: str) -> bool:
        """向单个 Webhook 发送消息"""
        payload = {"msgtype": "markdown", "markdown": {"text": markdown}}
        try:
            resp = requests.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            logging.warning("Webhook send failed (%s): %s", url[:50], exc)
            time.sleep(1)
            try:
                # 降级为纯文本
                payload = {"msgtype": "text", "text": {"content": markdown[:1500]}}
                resp = requests.post(url, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                return True
            except Exception as exc2:  # noqa: BLE001
                logging.warning("Webhook fallback failed (%s): %s", url[:50], exc2)
                return False
