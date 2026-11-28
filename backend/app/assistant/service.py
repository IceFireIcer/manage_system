"""悬浮助手只读问答编排（#65，无写工具）。"""

from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.agent import llm

logger = logging.getLogger(__name__)

FEATURE = llm.FEATURE_ASSISTANT
MAX_HISTORY_MESSAGES = 12

NOT_CONFIGURED_MESSAGE = (
    "助手尚未配置 LLM 模型。请联系管理员在「LLM 配置」中为「悬浮助手」"
    "绑定可用的供应商与提示词。"
)
FAILED_MESSAGE = "助手暂时无法生成回答，请稍后重试。"
EMPTY_MESSAGE = "助手没有返回内容，请换个问法或稍后再试。"


def _context_block(context: dict | None) -> str:
    if not context:
        return "{}"
    try:
        return json.dumps(context, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


def build_messages(
    system_prompt: str | None,
    context: dict | None,
    history: list,
    question: str,
) -> list[dict]:
    system = (system_prompt or "").strip() or llm.ASSISTANT_DEFAULT_SYSTEM_PROMPT
    system = (
        f"{system}\n\n"
        "以下为当前页面上下文与聚合摘要（只读，仅代表当前用户有权查看的数据）：\n"
        f"{_context_block(context)}"
    )
    messages: list[dict] = [{"role": "system", "content": system}]
    for message in history[-MAX_HISTORY_MESSAGES:]:
        role = getattr(message, "role", None)
        content = getattr(message, "content", None)
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})
    return messages


def chat(
    db: Session,
    *,
    question: str,
    context: dict | None,
    history: list,
) -> tuple[bool, str]:
    """返回 (是否已配置, 回答文本)；未配置时明确提示，不编造。"""
    config = llm.resolve_chat_config(db, FEATURE)
    if config is None:
        return False, NOT_CONFIGURED_MESSAGE
    messages = build_messages(config.system_prompt, context, history, question)
    try:
        answer = llm.run_chat(config, messages)
    except Exception:
        logger.warning("助手问答调用失败", exc_info=True)
        return True, FAILED_MESSAGE
    if not answer:
        return True, EMPTY_MESSAGE
    return True, answer


def status(db: Session) -> tuple[bool, str]:
    config = llm.resolve_chat_config(db, FEATURE)
    if config is None:
        return False, NOT_CONFIGURED_MESSAGE
    return True, "助手已就绪"