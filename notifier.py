from __future__ import annotations

import logging
import time
from typing import Optional

import requests


class Notifier:
    def __init__(self, webhook_url: str, timeout: int = 20) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout

    def send_markdown(self, markdown: str) -> bool:
        payload = {"msgtype": "markdown", "markdown": {"text": markdown}}
        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            logging.warning("Webhook send failed: %s", exc)
            time.sleep(1)
            try:
                payload = {"msgtype": "text", "text": {"content": markdown[:1500]}}
                resp = requests.post(self.webhook_url, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                return True
            except Exception as exc2:  # noqa: BLE001
                logging.warning("Webhook fallback failed: %s", exc2)
                return False
