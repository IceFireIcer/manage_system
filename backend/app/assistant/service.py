"""悬浮助手只读问答编排：可调用只读数据工具（#66）。"""

from __future__ import annotations

import json
import logging
import re

from sqlalchemy.orm import Session

from app.agent import llm
from app.assistant import tools as assistant_tools

logger = logging.getLogger(__name__)

FEATURE = llm.FEATURE_ASSISTANT
MAX_HISTORY_MESSAGES = 12
TOOL_SELECT_TIMEOUT = 8.0

NOT_CONFIGURED_MESSAGE = (
    "助手尚未配置 LLM 模型。请联系管理员在「LLM 配置」中为「悬浮助手」"
    "绑定可用的供应商与提示词。"
)
FAILED_MESSAGE = "助手暂时无法生成回答，请稍后重试。"
EMPTY_MESSAGE = "助手没有返回内容，请换个问法或稍后再试。"

TOOL_SELECTION_SYSTEM = (
    "你是只读数据工具选择器。可用工具如下：\n{tools}\n\n"
    "若回答该问题需要真实聚合数据，请只输出一行：TOOL: 工具名。"
    "若无需工具，请只输出：TOOL: none。不要输出其它内容。"
)


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
    extra: str = "",
) -> list[dict]:
    system = (system_prompt or "").strip() or llm.ASSISTANT_DEFAULT_SYSTEM_PROMPT
    system = (
        f"{system}\n\n"
        "以下为当前页面上下文与聚合摘要（只读，仅代表当前用户有权查看的数据）：\n"
        f"{_context_block(context)}"
    )
    if extra:
        system += extra
    messages: list[dict] = [{"role": "system", "content": system}]
    for message in history[-MAX_HISTORY_MESSAGES:]:
        role = getattr(message, "role", None)
        content = getattr(message, "content", None)
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})
    return messages


def _select_tool(config: llm.ChatConfig, question: str, context: dict | None) -> str | None:
    messages = [
        {
            "role": "system",
            "content": TOOL_SELECTION_SYSTEM.format(
                tools=assistant_tools.tool_prompt_text()
            ),
        },
        {
            "role": "user",
            "content": f"上下文：{_context_block(context)}\n问题：{question}",
        },
    ]
    try:
        raw = llm.run_chat(
            config,
            messages,
            timeout=min(config.timeout, TOOL_SELECT_TIMEOUT),
        )
    except Exception:
        logger.warning("助手工具选择失败", exc_info=True)
        return None
    text = (raw or "").lower()
    match = re.search(r"tool\s*[:：]\s*([a-z_]+)", text)
    if match is not None:
        name = match.group(1)
        if name in ("none", "no", "null", "无"):
            return None
        return name if name in assistant_tools.TOOL_MAP else None
    for name in assistant_tools.TOOL_MAP:
        if name in text:
            return name
    return None


def _tool_block(result: dict) -> str:
    if result.get("ok"):
        try:
            payload = json.dumps(result.get("data"), ensure_ascii=False)
        except (TypeError, ValueError):
            payload = "{}"
        return (
            "\n\n工具返回的真实数据（只读，回答必须以此为准，不得编造未给出的数值）：\n"
            f"{payload}"
        )
    error = result.get("error") or "工具执行失败"
    return (
        "\n\n工具调用未成功，请据实告知用户："
        f"{error}。不要编造数据，建议用户稍后重试或换个问题。"
    )


def chat(
    db: Session,
    *,
    user,
    role: str,
    class_id: int,
    question: str,
    context: dict | None,
    history: list,
) -> tuple[bool, str, list[dict]]:
    """返回 (是否已配置, 回答文本, 工具调用记录)。"""
    config = llm.resolve_chat_config(db, FEATURE)
    if config is None:
        return False, NOT_CONFIGURED_MESSAGE, []

    tool_calls: list[dict] = []
    extra = ""
    tool_name = _select_tool(config, question, context)
    if tool_name is not None:
        result = assistant_tools.run_tool(
            tool_name,
            assistant_tools.ToolContext(
                db=db, user=user, role=role, class_id=class_id
            ),
        )
        tool_calls.append(
            {
                "name": tool_name,
                "label": result.get("label") or tool_name,
                "ok": bool(result.get("ok")),
                "error": result.get("error"),
            }
        )
        extra = _tool_block(result)

    messages = build_messages(config.system_prompt, context, history, question, extra)
    try:
        answer = llm.run_chat(config, messages)
    except Exception:
        logger.warning("助手问答调用失败", exc_info=True)
        return True, FAILED_MESSAGE, tool_calls
    if not answer:
        return True, EMPTY_MESSAGE, tool_calls
    return True, answer, tool_calls


def status(db: Session) -> tuple[bool, str]:
    config = llm.resolve_chat_config(db, FEATURE)
    if config is None:
        return False, NOT_CONFIGURED_MESSAGE
    return True, "助手已就绪"