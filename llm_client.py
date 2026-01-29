from __future__ import annotations

import json
import logging
import random
import re
import time
from typing import List, Optional

import requests


SYSTEM_PROMPT = '''你是一名正在做竞品分析的**工具类产品经理**。

# 核心判断：【开箱即用测试】
问自己两个问题：
1. **这是给「最终用户」用的，还是给「开发者」用的？**
2. **用户需要懂代码才能跑起来吗？**

# 接受（通用型生产力工具 - Universal Productivity）
- **文档协作**: Notion, 笔记, PDF, 知识库, 思维导图
- **办公效能**: 会议, PPT, 表格, 项目管理, 邮件, 剪贴板
- **创意设计**: 图片生成, 视频剪辑, UI设计, 文案写作
- **自动化工具**: 工作流, 批量处理, 数据整理

# 拒绝（开发/基建/垂直行业）→ 返回 NULL
- **开发工具**: IDE, SDK, API, Deploy, DevOps, Netlify, Vercel
- **虚拟伴侣**: Companion, Waifu, Live2D, Virtual Friend, AI Girlfriend, Roleplay
- **垂直行业**: 股票交易(Trading), 医疗诊断(Medical), 法律咨询(Legal), 房地产(Real Estate)
- **基础设施**: ChatGPT, Claude, OpenAI, Azure, MCP

*Case Study*:
- 'HeyTraders' (量化交易) -> NULL
- 'AI Companion' (虚拟伴侣) -> NULL  
- 'ThinkFlow' (思维导图) -> Accept

# 产地判断（无罪推定 - 极端保守，默认海外）
- **CN**: 仅当搜索结果中**明确出现**以下任一证据：
  * 官网有ICP备案号（如"京ICP备"、"粤ICP备"）
  * 公司地址**明确写** Beijing/Shanghai/Shenzhen/Hangzhou/Guangzhou
  * 公司名称包含 "科技有限公司"、"网络技术"
- **Global**: 默认值！99% 情况应该标 Global

⚠️ 以下都**不算**中国产品的证据，必须标 Global：
- 名字像中文（如 ThinkFlow）
- 用了 Live2D/Anime 风格
- 团队有华人开发者
- 面向亚洲市场
- 来自 Hacker News（几乎都是海外）

# 热度验证（Social Proof Check）
在推荐前，检查搜索结果中是否有**社会认同**信号：
- ✓ 用户量: "1M+ users", "50k teams", "被 XX 公司使用"
- ✓ 融资: "Series A", "raised $10M", "YC backed"
- ✓ 媒体背书: "TechCrunch", "Verge", "被 XX 报道"
- ✓ 榜单: "No.1 on Product Hunt", "1000+ stars"

**如果搜索结果显示该产品：**
- 没有任何媒体报道
- 没有用户数据
- 仅仅是一个刚上线的 Landing Page
→ **判定为热度不足，返回 NULL**

# 推荐语（PM视角）
不要堆砌功能，点出**核心价值**或**交互亮点**：
- ✓ "通过AI简化了传统CRM的录入流程"
- ✓ "把会议纪要生成从30分钟压缩到1分钟"
- ✗ "支持多种功能包括XXX、YYY、ZZZ"

# 输出规则
1. 简体中文，英文只允许产品名
2. 60-80 字，痛点→方案→价值
3. 必须填 origin 字段
4. 热度不足的产品返回 NULL

# 示例

输入: "Gamma - AI presentation maker..."
输出: {"one_sentence_intro_cn": "做PPT要花几小时调版式，这工具输入大纲就能自动排版配图，把制作时间压缩到几分钟。", "origin": "Global"}

输入: "Kimi - by Moonshot AI, headquarters in Beijing..."
输出: {"one_sentence_intro_cn": "长文档阅读费时费力，这工具支持20万字上下文，一键提炼要点生成摘要。", "origin": "CN"}

输入: "Blink Agent Builder - Build AI agents..."
输出: {"one_sentence_intro_cn": "NULL", "origin": "Global"}

# 输出格式
直接输出 JSON：{"one_sentence_intro_cn": "...", "origin": "CN|Global"}
'''


class LLMClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float = 0.5,
        timeout: int = 90,  # 增加超时时间
        max_retries: int = 3,  # 减少重试次数（避免太慢）
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries

    @staticmethod
    def _contains_chinese(text: str) -> bool:
        """检查文本是否包含中文字符"""
        if not text:
            return False
        return bool(re.search(r'[\u4e00-\u9fa5]', text))

    @staticmethod
    def _chinese_ratio(text: str) -> float:
        """计算中文字符占比"""
        if not text:
            return 0.0
        chinese_chars = len(re.findall(r'[\u4e00-\u9fa5]', text))
        # 只计算非空白、非标点的有效字符
        effective_chars = len(re.sub(r'[\s\.,!?;:\-\"\'\(\)\[\]{}]', '', text))
        if effective_chars == 0:
            return 0.0
        return chinese_chars / effective_chars

    @staticmethod
    def _needs_rewrite(text: str) -> bool:
        if not text:
            return True
        lowered = text.lower()
        # 垃圾通用词检测
        garbage_phrases = ["text to video generator", "ai writer free"]
        if any(p in lowered for p in garbage_phrases):
            return True
        # 元数据噪音检测
        metadata_noise = [
            "1 day ago", "2 days ago", "3 days ago", "days ago", "hours ago",
            "source:", "updated:", "stars", "⭐", "created:", "discussion",
            "comments", "| link", "read more", "click here",
        ]
        if any(noise in lowered for noise in metadata_noise):
            return True
        # 中文占比检测：如果中文字符占比低于 40%，说明没有正确翻译
        if not LLMClient._contains_chinese(text):
            return True
        if LLMClient._chinese_ratio(text) < 0.4:
            return True
        return False

    @staticmethod
    def _postprocess_intro(text: str) -> str:
        """后处理：如果检测到翻译失败，添加前缀标记"""
        if not text:
            return "(自动翻译失败) 暂无中文介绍"
        if not LLMClient._contains_chinese(text):
            return f"(自动翻译失败) {text}"
        if LLMClient._chinese_ratio(text) < 0.3:
            return f"(翻译不完整) {text}"
        return text

    @staticmethod
    def _is_invalid_output(text: str) -> bool:
        if not text:
            return True
        return "error: 无效数据" in text.lower()

    def _request(self, messages: List[dict]) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {"model": self.model, "messages": messages, "temperature": self.temperature}
        last_err = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
                # 检查 429 Rate Limit
                if resp.status_code == 429:
                    wait_time = min(30 * attempt, 120)  # 更长的等待时间
                    logging.warning("Rate limited (429), waiting %ds...", wait_time)
                    time.sleep(wait_time)
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                logging.debug("LLM request attempt %d failed: %s", attempt, exc)
                time.sleep(min(2**attempt, 10) + random.random())
        raise RuntimeError("LLM request failed") from last_err

    @staticmethod
    def _clean_response(text: str) -> str:
        if not text:
            return ""
        return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    @staticmethod
    def _extract_json(text: str):
        text = LLMClient._clean_response(text)
        if not text:
            raise ValueError("Empty LLM response")
        # 移除 markdown 代码块标记
        text = re.sub(r"```json\s*", "", text)
        text = re.sub(r"```\s*", "", text)
        text = text.strip()
        # 直接解析
        try:
            return json.loads(text)
        except Exception:
            pass
        # 尝试找 JSON 数组
        match = re.search(r"\[[\s\S]*?\](?=\s*$|\s*\n|$)", text, re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
        # 尝试找 JSON 对象
        match = re.search(r"\{[\s\S]*?\}(?=\s*$|\s*\n|$)", text, re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
        # 最后尝试贪婪匹配
        match = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if not match:
            raise ValueError("No JSON found in LLM response")
        return json.loads(match.group(1))

    def generate_recommendation_prompt(
        self,
        candidates: List[dict],
        source_name: str,
        extra_instruction: str = "",
    ) -> str:
        return (
            f"请用中文回答。\n\n"
            f"以下是 {len(candidates)} 个产品，来源：{source_name}。\n"
            f"请选择一个最实用的，写一段中文推荐语。\n\n"
            f"产品列表：\n{json.dumps(candidates, ensure_ascii=False)}\n\n"
            "要求：\n"
            "1. 推荐语必须是中文（60-80字）\n"
            "2. 结构：用户痛点 → 工具方案 → 核心价值\n"
            "3. 禁止输出英文句子\n\n"
            "输出格式（JSON）：\n"
            '{"name": "产品名", "url": "链接", "one_sentence_intro_cn": "中文推荐语"}\n\n'
            "示例输出：\n"
            '{"name": "Raycast", "url": "https://raycast.com", '
            '"one_sentence_intro_cn": "每天在 Mac 上切换应用、搜索文件要点无数次鼠标，这启动器一个快捷键搞定一切，键盘党效率翻倍。"}'
            + (f"\n\n{extra_instruction}" if extra_instruction else "")
        )

    def parse_llm_response(self, content: str) -> Optional[dict]:
        if not content:
            return None
        content = self._clean_response(content)
        if not content:
            return None
        normalized = content.strip().lower()
        if normalized in {"none", "null", "no", "n/a"}:
            return None
        data = self._extract_json(content)
        if isinstance(data, dict) and data.get("name"):
            return data
        return None

    def select_best(
        self,
        candidates: List[dict],
        source_name: str,
        extra_instruction: str = "",
    ) -> Optional[dict]:
        if not candidates:
            return None
        user_prompt = self.generate_recommendation_prompt(candidates, source_name, extra_instruction)
        best_result = None
        for attempt in range(1, self.max_retries + 1):
            content = self._request(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ]
            )
            try:
                parsed = self.parse_llm_response(content)
                if parsed:
                    reason = parsed.get("one_sentence_intro_cn", "")
                    if self._is_invalid_output(reason):
                        continue
                    # 如果质量合格，直接返回
                    if not self._needs_rewrite(reason):
                        return parsed
                    # 保存一个备选结果（即使质量不佳）
                    if best_result is None:
                        best_result = parsed
            except Exception as exc:  # noqa: BLE001
                logging.warning("LLM JSON parse failed (%s): %s", source_name, exc)
                time.sleep(min(2**attempt, 6) + random.random())
        # 如果所有尝试都失败，返回备选结果并添加后处理标记
        if best_result:
            reason = best_result.get("one_sentence_intro_cn", "")
            best_result["one_sentence_intro_cn"] = self._postprocess_intro(reason)
        return best_result

    def select_top_n(
        self,
        candidates: List[dict],
        min_items: int = 3,
        max_items: int = 4,
    ) -> List[dict]:
        if not candidates:
            return []
        user_prompt = (
            f"请用中文回答。你是产品经理，在做竞品分析。\n\n"
            f"以下是 {len(candidates)} 个产品，请选择 {min_items}-{max_items} 个**给最终用户用的**效率工具。\n\n"
            f"产品列表：\n{json.dumps(candidates, ensure_ascii=False)}\n\n"
            "【接受 - 最终用户工具】\n"
            "文档协作、会议、PPT、表格、项目管理、图片视频生成、设计、建站\n\n"
            "【拒绝 - 开发者工具 → 返回 NULL】\n"
            "帮人写代码的、帮人发版的、帮人造App的、运维监控的、Agent Builder\n\n"
            "【产地判断 - 极端保守】\n"
            "CN=必须有ICP备案号或明确中国总部地址, Global=默认值(名字像中文/华人团队都不算)\n\n"
            "【推荐语 - PM视角】\n"
            "点出核心价值，不要堆砌功能，60-80字\n\n"
            "【输出格式】JSON数组：\n"
            '[{"name": "产品名", "url": "链接", "one_sentence_intro_cn": "推荐语", "origin": "CN或Global", "source": "来源"}]'
        )
        best_result = []
        for attempt in range(1, self.max_retries + 1):
            content = self._request(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ]
            )
            try:
                data = self._extract_json(content)
                if isinstance(data, list):
                    valid_items = []
                    for item in data:
                        if not isinstance(item, dict) or not item.get("name"):
                            continue
                        reason = item.get("one_sentence_intro_cn", "")
                        # 【开发工具过滤】LLM 返回 NULL 表示是开发者工具，跳过
                        if reason.strip().upper() == "NULL":
                            logging.info("Skipping rejected item (LLM marked NULL): %s", item.get("name"))
                            continue
                        # 后处理：添加翻译失败标记
                        item["one_sentence_intro_cn"] = self._postprocess_intro(reason)
                        # 【产地处理】默认为 Global
                        if "origin" not in item or item["origin"] not in ("CN", "Global"):
                            item["origin"] = "Global"
                        valid_items.append(item)
                    # 如果结果质量较好，直接返回
                    good_items = [
                        i for i in valid_items
                        if not i.get("one_sentence_intro_cn", "").startswith("(")
                    ]
                    if len(good_items) >= min_items:
                        return valid_items
                    # 保存备选结果
                    if len(valid_items) > len(best_result):
                        best_result = valid_items
            except Exception as exc:  # noqa: BLE001
                logging.warning("LLM JSON parse failed (pool): %s", exc)
                time.sleep(min(2**attempt, 6) + random.random())
        return best_result

    def one_line_summary(self, name: str, tagline: str, tags: str = "") -> str:
        """单产品翻译：生成中文推荐语"""
        prompt = (
            f"产品：{name}\n"
            f"英文：{tagline}\n\n"
            "请用中文写一段推荐语（60-80字），结构：痛点 → 方案 → 价值。\n"
            "只输出推荐语本身，不要输出解释或注释。"
        )
        try:
            content = self._request(
                [
                    {"role": "system", "content": "你是中文科技编辑。只输出翻译结果，不要解释。"},
                    {"role": "user", "content": prompt},
                ]
            )
            result = self._clean_response(content).strip()
            # 只取第一行（去掉注释）
            first_line = result.split('\n')[0].strip()
            # 移除引号和括号开头的注释
            first_line = first_line.strip('"').strip("'").strip()
            if first_line.startswith("（"):
                first_line = result.split('\n')[0].strip()
            # 检查是否包含中文
            if self._contains_chinese(first_line) and len(first_line) > 15:
                return first_line
        except Exception:
            pass
        return f"(待翻译) {tagline[:60]}" if tagline else f"{name} - AI 工具"
