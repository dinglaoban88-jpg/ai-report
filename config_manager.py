from __future__ import annotations

import json
import os
from typing import Any

DEFAULT_CONFIG = {
    "webhook_url": "",
    "schedule_time": "09:00",
    "schedule_enabled": False,
    "schedule_started_at": "",
    "schedule_expires_at": "",
    "llm_api_key": "",
    "llm_base_url": "https://api.deepseek.com",
    "llm_model": "deepseek-chat",
    "ph_api_token": "",
    "tavily_api_key": "",
}


def _env_default(key: str, fallback: str = "") -> str:
    return os.getenv(key, "").strip() or fallback


def load_config(path: str = "config.json") -> dict[str, Any]:
    """
    加载配置，优先级：环境变量 > config.json > 默认值
    
    支持的环境变量：
    - DEEPSEEK_API_KEY / LLM_API_KEY / OPENAI_API_KEY: LLM API Key
    - LLM_BASE_URL: LLM API Base URL
    - LLM_MODEL: LLM 模型名称
    - TAVILY_API_KEY: Tavily 搜索 API Key
    - REPORT_WEBHOOK_URL / FEISHU_WEBHOOK: Webhook 推送地址
    """
    def _get_llm_key() -> str:
        return (_env_default("DEEPSEEK_API_KEY") or 
                _env_default("LLM_API_KEY") or 
                _env_default("OPENAI_API_KEY") or
                _env_default("OPENROUTER_API_KEY"))
    
    def _get_webhook() -> str:
        return (_env_default("REPORT_WEBHOOK_URL") or
                _env_default("FEISHU_WEBHOOK") or
                _env_default("SLACK_WEBHOOK"))
    
    if not os.path.exists(path):
        merged = DEFAULT_CONFIG.copy()
        merged["webhook_url"] = _get_webhook()
        merged["llm_api_key"] = _get_llm_key()
        merged["llm_base_url"] = _env_default("LLM_BASE_URL", DEFAULT_CONFIG["llm_base_url"])
        merged["llm_model"] = _env_default("LLM_MODEL", DEFAULT_CONFIG["llm_model"])
        merged["ph_api_token"] = _env_default("PH_API_TOKEN")
        merged["tavily_api_key"] = _env_default("TAVILY_API_KEY")
        return merged
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            merged = DEFAULT_CONFIG.copy()
            merged.update({k: v for k, v in data.items() if v is not None})
            # 环境变量优先级更高
            env_llm_key = _get_llm_key()
            if env_llm_key:
                merged["llm_api_key"] = env_llm_key
            env_webhook = _get_webhook()
            if env_webhook:
                merged["webhook_url"] = env_webhook
            env_tavily = _env_default("TAVILY_API_KEY")
            if env_tavily:
                merged["tavily_api_key"] = env_tavily
            if not merged.get("llm_base_url"):
                merged["llm_base_url"] = _env_default(
                    "LLM_BASE_URL", DEFAULT_CONFIG["llm_base_url"]
                )
            if not merged.get("llm_model"):
                merged["llm_model"] = _env_default("LLM_MODEL", DEFAULT_CONFIG["llm_model"])
            if not merged.get("ph_api_token"):
                merged["ph_api_token"] = _env_default("PH_API_TOKEN")
            return merged
    except Exception:
        pass
    return DEFAULT_CONFIG.copy()


def save_config(config: dict[str, Any], path: str = "config.json") -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
