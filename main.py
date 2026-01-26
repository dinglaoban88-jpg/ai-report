from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Callable, Optional, Tuple

import schedule

from config_manager import load_config
from curator import Curator
from llm_client import LLMClient
from notifier import Notifier
from reporter import generate_markdown
from scraper import Scraper
from utils.history_manager import HistoryManager


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


class _CallbackHandler(logging.Handler):
    def __init__(self, callback: Callable[[str], None]) -> None:
        super().__init__()
        self.callback = callback
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.callback(self.format(record))
        except Exception:
            pass


def run_daily_job(
    output_dir: str = "reports",
    webhook_url: Optional[str] = None,
    send_webhook: bool = True,
    log_callback: Optional[Callable[[str], None]] = None,
) -> Tuple[str, str]:
    # 从环境变量或配置文件加载配置
    cfg = load_config()
    api_key = cfg.get("llm_api_key", "").strip()
    base_url = cfg.get("llm_base_url", "https://api.deepseek.com").strip()
    model = cfg.get("llm_model", "deepseek-chat").strip()
    
    if not api_key:
        raise RuntimeError("Missing LLM API Key. Set DEEPSEEK_API_KEY or LLM_API_KEY environment variable.")

    handler = None
    if log_callback:
        handler = _CallbackHandler(log_callback)
        logging.getLogger().addHandler(handler)

    # 初始化历史管理器（永久去重）
    history = HistoryManager(history_file="data/history.json")
    logging.info("History loaded: %d items", history.get_stats()["total"])

    try:
        llm = LLMClient(api_key=api_key, base_url=base_url, model=model)

        with Scraper(headless=True) as scraper:
            curator = Curator(
                scraper=scraper,
                llm=llm,
                store_path=os.path.join(os.path.dirname(__file__), "recommendations.json"),
                history=history,  # 传入历史管理器
            )
            logging.info("正在抓取今日新品...")
            products = curator.get_today_news()
            logging.info("新品抓取完成: %s 条", len(products))

            logging.info("正在生成精选推荐...")
            curated = curator.curate()
            logging.info("精选推荐完成: %s 条", len(curated))

            report_md = generate_markdown(products, curated)

        os.makedirs(output_dir, exist_ok=True)
        filename = f"{datetime.now().strftime('%Y-%m-%d')}_ai_report.md"
        path = os.path.join(output_dir, filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write(report_md)

        logging.info("Report saved to %s", path)

        # 【永久去重】保存本次推荐的产品到历史
        if curated:
            history.save_recommendations(curated)
            logging.info("History updated: %d items total", history.get_stats()["total"])

        if webhook_url is None:
            webhook_url = cfg.get("webhook_url", "").strip()
        if send_webhook and webhook_url:
            notifier = Notifier(webhook_url=webhook_url)
            notifier.send_markdown(report_md)
        return report_md, path
    finally:
        if handler:
            logging.getLogger().removeHandler(handler)


def run_once(output_dir: str = "reports") -> str:
    _, path = run_daily_job(output_dir=output_dir, send_webhook=True)
    return path


def run_scheduler(at_time: str = "09:00", output_dir: str = "reports") -> None:
    schedule.every().day.at(at_time).do(run_once, output_dir=output_dir)
    logging.info("Scheduler started. Daily report at %s", at_time)
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    run_once(output_dir="reports")
