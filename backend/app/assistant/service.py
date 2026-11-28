"""悬浮助手问答编排：只读工具与安全写/预填工具（#67）。"""

from __future__ import annotations

import json
import logging
import re
from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app import business_time
from app.agent import llm
from app.assistant import actions as assistant_actions
from app.assistant import tools as assistant_tools
from app.config import Settings, get_settings
from app.models import AssistantThread, SystemSetting

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
    "你是悬浮助手的工具选择器。只读工具如下：\n{read_tools}\n\n"
    "操作与预填工具如下：\n{action_tools}\n\n"
    "禁止：账号、角色、权限、LLM 或系统设置类操作，禁止直接写入库存或账本，"
    "禁止调用未列出的工具。\n"
    "若需要工具，只输出一行 JSON：{{\"tool\": \"工具名\", \"args\": {{...}}}}；"
    "若无需工具，只输出：{{\"tool\": \"none\"}}。不要输出其它内容。"
)

DEGRADED_EXTRA = (
    "\n\n当前 LLM 供应商不支持工具调用，已降级为只读问答。"
    "请只依据上下文回答，并给出对应页面路径建议\n"
    "/dashboard 工作台、/overview 库存总览、/requisition 领用登记、"
    "/records 记录查询、/incidents 异常上报、/restock 补货、"
    "/consumption 消耗汇总、/courses 课程配方。\n"
    "不要声称已执行或已提交任何操作。"
)

FORBIDDEN_REQUEST_EXTRA = (
    "\n\n用户请求涉及助手无权执行的操作（账号/角色/权限/LLM/系统设置或直接写入账本），"
    "请说明助手不能执行，并建议用户前往对应设置页或业务页面手动操作。"
)

UNKNOWN_TOOL_EXTRA = (
    "\n\n工具选择返回了未注册的工具，本轮不执行任何操作。"
    "请据实说明助手无法完成该请求，不要编造结果。"
)


ASSISTANT_RETENTION_KEY = "assistant_retention_days"


def retention_days(db: Session, settings: Settings | None = None) -> int:
    row = db.get(SystemSetting, ASSISTANT_RETENTION_KEY)
    if row is not None:
        try:
            return max(0, int(row.value))
        except (TypeError, ValueError):
            pass
    return max(0, (settings or get_settings()).assistant_retention_days)


def trim_threads(db: Session, settings: Settings | None = None) -> int:
    days = retention_days(db, settings)
    if days <= 0:
        return 0
    cutoff = business_time.now() - timedelta(days=days)
    result = db.execute(
        delete(AssistantThread).where(AssistantThread.last_message_at < cutoff)
    )
    return result.rowcount or 0


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


def _extract_payload(text: str) -> dict | None:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced is not None else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            candidate = text[start : end + 1]
    if candidate is None:
        return None
    try:
        parsed = json.loads(candidate)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _select_tool(
    config: llm.ChatConfig, question: str, context: dict | None
) -> dict | None:
    messages = [
        {
            "role": "system",
            "content": TOOL_SELECTION_SYSTEM.format(
                read_tools=assistant_tools.tool_prompt_text(),
                action_tools=assistant_tools.action_tool_prompt_text(),
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
    text = raw or ""
    payload = _extract_payload(text)
    if payload is not None:
        name = str(payload.get("tool") or "").strip().lower()
        if name in ("", "none", "no", "null", "无"):
            return None
        args = payload.get("args")
        return {"tool": name, "args": args if isinstance(args, dict) else {}}
    lowered = text.lower()
    match = re.search(r"tool\s*[:：]\s*([a-z_]+)", lowered)
    if match is not None:
        name = match.group(1)
        if name in ("none", "no", "null", "无"):
            return None
        return {"tool": name, "args": {}}
    for name in assistant_tools.TOOL_MAP:
        if name in lowered:
            return {"tool": name, "args": {}}
    for name in assistant_tools.ACTION_TOOL_MAP:
        if name in lowered:
            return {"tool": name, "args": {}}
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


def _action_block(
    spec: assistant_tools.ActionToolSpec, preview: dict
) -> str:
    summary = preview.get("summary") or spec.label
    if spec.requires_confirmation:
        return (
            f"\n\n已为用户准备待确认操作（尚未执行）：{summary}。"
            "请在回答中告知用户需在操作卡片上点击确认后才会生效，"
            "不得声称已经完成修改或提交。"
        )
    return (
        f"\n\n已为用户生成页面预填链接（尚未提交）：{summary}。"
        "请引导用户点击卡片前往对应页面自行填写并提交，"
        "不得声称已经提交或已修改任何数据。"
    )


def _action_failed_block(name: str, error: str) -> str:
    return (
        f"\n\n操作工具 {name} 未能准备：{error}。"
        "请据实告知用户，不得声称已执行。"
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
    thread_id: int | None = None,
) -> tuple[bool, str, list[dict], list[dict]]:
    """返回 (是否已配置, 回答文本, 工具调用记录, 本轮动作卡片)。"""
    config = llm.resolve_chat_config(db, FEATURE)
    if config is None:
        return False, NOT_CONFIGURED_MESSAGE, [], []

    tool_calls: list[dict] = []
    actions: list[dict] = []
    extra = ""
    ctx = assistant_tools.ToolContext(
        db=db, user=user, role=role, class_id=class_id
    )
    if not config.supports_tools:
        extra = DEGRADED_EXTRA
    else:
        selection = _select_tool(config, question, context)
        if selection is not None:
            name = selection["tool"]
            raw_args = selection["args"]
            if assistant_tools.is_forbidden_tool(name):
                extra = FORBIDDEN_REQUEST_EXTRA
            elif name in assistant_tools.TOOL_MAP:
                result = assistant_tools.run_tool(name, ctx)
                tool_calls.append(
                    {
                        "name": name,
                        "label": result.get("label") or name,
                        "ok": bool(result.get("ok")),
                        "error": result.get("error"),
                    }
                )
                extra = _tool_block(result)
            elif name in assistant_tools.ACTION_TOOL_MAP:
                try:
                    spec, args, preview = assistant_tools.prepare_action(
                        name, ctx, raw_args
                    )
                except assistant_tools.ToolActionError as exc:
                    tool_calls.append(
                        {
                            "name": name,
                            "label": assistant_tools.ACTION_TOOL_MAP[name].label,
                            "ok": False,
                            "error": str(exc),
                        }
                    )
                    extra = _action_failed_block(name, str(exc))
                else:
                    action = assistant_actions.create_action(
                        db,
                        thread_id=thread_id,
                        user=user,
                        class_id=class_id,
                        spec=spec,
                        args=args,
                        preview=preview,
                    )
                    actions.append(assistant_actions.serialize(action))
                    extra = _action_block(spec, preview)
            else:
                extra = UNKNOWN_TOOL_EXTRA

    messages = build_messages(config.system_prompt, context, history, question, extra)
    try:
        answer = llm.run_chat(config, messages)
    except Exception:
        logger.warning("助手问答调用失败", exc_info=True)
        return True, FAILED_MESSAGE, tool_calls, actions
    if not answer:
        return True, EMPTY_MESSAGE, tool_calls, actions
    return True, answer, tool_calls, actions


def status(db: Session) -> tuple[bool, str]:
    config = llm.resolve_chat_config(db, FEATURE)
    if config is None:
        return False, NOT_CONFIGURED_MESSAGE
    return True, "助手已就绪"
