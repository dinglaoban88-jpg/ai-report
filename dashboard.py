from __future__ import annotations

import os
from datetime import datetime, time as time_cls, timedelta
from typing import Optional

import streamlit as st
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from config_manager import load_config, save_config
from main import run_daily_job
from notifier import Notifier


def _latest_report_path(report_dir: str = "reports") -> Optional[str]:
    if not os.path.isdir(report_dir):
        return None
    files = [
        os.path.join(report_dir, name)
        for name in os.listdir(report_dir)
        if name.endswith("_ai_report.md")
    ]
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def _load_latest_report(report_dir: str = "reports") -> Optional[str]:
    path = _latest_report_path(report_dir)
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


st.set_page_config(page_title="AI 情报局 - 指挥中心", layout="wide")
st.title("AI 情报局 - 指挥中心")

config = load_config()
if "logs" not in st.session_state:
    st.session_state["logs"] = []
if "report_md" not in st.session_state:
    st.session_state["report_md"] = _load_latest_report()

with st.sidebar:
    st.subheader("配置中心")
    schedule_time = st.time_input(
        "定时设置",
        value=time_cls.fromisoformat(config.get("schedule_time", "09:00")),
    )
    webhook_url = st.text_input("Webhook 配置", value=config.get("webhook_url", ""))
    st.markdown("---")
    st.caption("LLM 设置")
    llm_model = st.text_input("Model Name", value=config.get("llm_model", "deepseek-r1"))
    llm_base_url = st.text_input("Base URL", value=config.get("llm_base_url", "http://localhost:11434/v1"))
    schedule_enabled = st.toggle("启用后台定时", value=bool(config.get("schedule_enabled")))
    if st.button("保存配置"):
        config["schedule_time"] = schedule_time.strftime("%H:%M")
        config["webhook_url"] = webhook_url.strip()
        config["llm_model"] = llm_model.strip()
        config["llm_base_url"] = llm_base_url.strip()
        if schedule_enabled and not config.get("schedule_enabled"):
            now = datetime.now()
            config["schedule_enabled"] = True
            config["schedule_started_at"] = now.isoformat()
            config["schedule_expires_at"] = (now + timedelta(days=90)).isoformat()
        elif not schedule_enabled:
            config["schedule_enabled"] = False
        save_config(config)
        st.success("配置已保存")

    st.markdown("---")
    st.caption("运行状态（本地后台）")
    if config.get("schedule_enabled"):
        expires = config.get("schedule_expires_at") or "未知"
        st.success(f"已启用，截止: {expires}")
    else:
        st.info("未启用")

st.subheader("即时运行")

log_expander = st.expander("运行日志", expanded=True)
log_placeholder = log_expander.empty()
log_placeholder.code("\n".join(st.session_state["logs"]))


def _push_log(message: str) -> None:
    st.session_state["logs"].append(message)
    log_placeholder.code("\n".join(st.session_state["logs"]))


col_run, col_send = st.columns([1, 1])
with col_run:
    if st.button("立即运行"):
        st.session_state["logs"] = []
        log_placeholder.code("")
        try:
            report_md, _ = run_daily_job(
                output_dir="reports",
                webhook_url=config.get("webhook_url") or None,
                send_webhook=True,
                log_callback=_push_log,
            )
            st.session_state["report_md"] = report_md
            st.success("运行完成")
        except Exception as exc:  # noqa: BLE001
            st.error(f"运行失败: {exc}")

with col_send:
    if st.button("Test Send"):
        report_md = st.session_state.get("report_md") or _load_latest_report()
        if not report_md:
            st.warning("暂无可发送的日报，请先运行一次。")
        elif not config.get("webhook_url"):
            st.warning("请先在侧边栏配置 Webhook。")
        else:
            notifier = Notifier(webhook_url=config.get("webhook_url"))
            ok = notifier.send_markdown(report_md)
            if ok:
                st.success("发送成功")
            else:
                st.error("发送失败，请检查 Webhook")

st.markdown("---")
st.subheader("调试爬虫")
if st.button("📸 运行爬虫并截图"):
    targets = {
        "toolify": "https://www.toolify.ai/zh/new",
        "ph": "https://www.producthunt.com/categories/ai-agents",
        "aibase": "https://app.aibase.com/zh",
        "aicpb": "https://www.aicpb.com/ai-rankings/products/global-ai-rankings",
    }
    screenshot_dir = "debug_screens"
    os.makedirs(screenshot_dir, exist_ok=True)
    images = {}
    auth_state_path = os.path.join(os.path.dirname(__file__), "auth_state.json")
    auth_profile_dir = os.path.join(os.path.dirname(__file__), "auth_profile")
    with sync_playwright() as p:
        if os.path.isdir(auth_profile_dir):
            context = p.chromium.launch_persistent_context(
                auth_profile_dir,
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1280, "height": 720},
            )
            browser = None
        else:
            browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            context = browser.new_context(
                viewport={"width": 1280, "height": 720},
                storage_state=auth_state_path if os.path.exists(auth_state_path) else None,
            )
        for name, url in targets.items():
            page = context.new_page()
            Stealth().apply_stealth_sync(page)
            page.goto(url, wait_until="domcontentloaded")
            if name == "aicpb":
                try:
                    page.wait_for_selector("tbody tr", timeout=15000)
                except Exception:
                    page.wait_for_timeout(5000)
            else:
                page.wait_for_timeout(5000)
            filename = {
                "toolify": "debug_toolify.png",
                "ph": "debug_ph.png",
                "aibase": "debug_aibase.png",
                "aicpb": "debug_aicpb.png",
            }.get(name, f"debug_{name}.png")
            path = os.path.join(screenshot_dir, filename)
            page.screenshot(path=path, full_page=True)
            images[name] = path
            page.close()
        context.close()
        if browser:
            browser.close()
    for name, path in images.items():
        st.image(path, caption=name, use_container_width=True)

st.markdown("---")
st.subheader("日报预览")
if st.session_state.get("report_md"):
    st.markdown(st.session_state["report_md"], unsafe_allow_html=False)
else:
    st.info("暂无日报，请点击“立即运行”。")
