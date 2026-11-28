import logging
import re
import time
from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI

from app.agent.rules import Evaluation
from app.config import Settings
from app.request_context import log_tag

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
LLM_TIMEOUT_SECONDS = 3.0

RATIONALE_SOURCE_LLM = "llm"
RATIONALE_SOURCE_RULES = "rules"

_RATIONALE_ANCHORS = (
    ("current_stock", ("当前库存",), None),
    ("safety_stock", ("安全库存线",), None),
    ("gap", ("库存缺口",), None),
    ("q7", ("近 7 天净消耗", "近7天净消耗", "净消耗"), None),
    ("rate", ("日均速率",), 2),
    ("days_of_supply", ("可支撑天数", "支撑天数"), 1),
    ("recommended_quantity", ("建议补货量", "建议补货"), None),
)

TIER_KEYWORDS = {
    "urgent": "存在断供风险，建议立即补货",
    "moderate": "临近安全库存线，建议尽快补货",
    "observe": "可结合后续领用情况观察并及时补货",
}

SYSTEM_PROMPT = (
    "你是班级实训耗材管理系统的补货分析助手。"
    "请基于给定数据，用 2 到 4 句简体中文说明该耗材的补货判断依据。"
    "必须引用给定的当前库存、安全库存线、近 7 天净消耗、日均速率、"
    "可支撑天数与建议补货量，不得编造未给出的数据，不要使用 Markdown。"
)

TEACHING_UNLINKED_NOTE = "未关联课次"

TEACHING_SYSTEM_PROMPT = (
    "你是班级实训耗材管理系统的教学影响助手。"
    "请只用给定的结构化数据，用 1 到 2 句简体中文说明不补货对已排课次的影响。"
    "必须写明覆盖课次数、涉及学生人数与预计缺口，"
    "不得编造未给出的课次、人数或数量，不要使用 Markdown。"
)


@dataclass(frozen=True)
class TeachingConsequenceContext:
    name: str
    unit: str
    unlinked: bool
    session_count: int
    headcount_total: int
    expected_demand: int
    expected_gap: int


@dataclass(frozen=True)
class RationaleContext:
    name: str
    unit: str
    evaluation: Evaluation


@dataclass(frozen=True)
class RationaleResult:
    text: str
    source: str
    degraded: bool


def _anchor_value(text: str, keywords: tuple[str, ...]) -> float | None:
    for keyword in keywords:
        match = re.search(
            re.escape(keyword) + r"[^\d]{0,4}?(-?\d+(?:\.\d+)?)", text
        )
        if match is not None:
            return float(match.group(1))
    return None


def validate_rationale(text: str, evaluation: Evaluation) -> bool:
    for field, keywords, places in _RATIONALE_ANCHORS:
        expected = getattr(evaluation, field)
        actual = _anchor_value(text, keywords)
        if actual is None or expected is None:
            continue
        if places is None:
            if int(round(actual)) != int(expected):
                return False
        elif round(actual, places) != round(float(expected), places):
            return False
    return True


class RationaleGenerator(Protocol):
    def generate(self, context: RationaleContext) -> str: ...


def format_template(context: RationaleContext) -> str:
    evaluation = context.evaluation
    unit = context.unit
    if evaluation.consumption_state == "none":
        consumption = evaluation.consumption_note
    else:
        consumption = (
            f"{evaluation.consumption_note}"
            f"（日均 {evaluation.rate:.2f}{unit}）"
        )
    reasons = "；".join(evaluation.reasons) or "库存状态触发"

    if evaluation.current_stock >= evaluation.safety_stock:
        return (
            f"{context.name}当前库存 {evaluation.current_stock}{unit}，不低于安全库存线 "
            f"{evaluation.safety_stock}{unit}；{consumption}，命中「{reasons}」，"
            f"按一周用量建议补货 {evaluation.recommended_quantity}{unit}。"
        )

    summary = (
        f"{context.name}当前库存 {evaluation.current_stock}{unit}，低于安全库存线 "
        f"{evaluation.safety_stock}{unit}；{consumption}"
    )
    if evaluation.days_of_supply is None:
        summary += (
            f"，消耗速率为零，可支撑天数视为无穷，库存缺口 {evaluation.gap}{unit}"
        )
    else:
        summary += f"，仅可支撑 {evaluation.days_of_supply:.1f} 天"
    return (
        f"{summary}，命中「{reasons}」，{TIER_KEYWORDS[evaluation.tier]} "
        f"{evaluation.recommended_quantity}{unit}。"
    )


def build_prompt(context: RationaleContext) -> str:
    evaluation = context.evaluation
    days = "无穷大" if evaluation.days_of_supply is None else f"{evaluation.days_of_supply:.1f}"
    return (
        f"耗材：{context.name}\n"
        f"单位：{context.unit or '件'}\n"
        f"分级：{evaluation.tier}\n"
        f"当前库存：{evaluation.current_stock}\n"
        f"安全库存线：{evaluation.safety_stock}\n"
        f"库存缺口：{evaluation.gap}\n"
        f"近 7 天净消耗：{evaluation.q7}\n"
        f"再前 7 天净消耗：{evaluation.prev_q7}\n"
        f"日均速率：{evaluation.rate:.2f}\n"
        f"可支撑天数：{days}\n"
        f"建议补货量：{evaluation.recommended_quantity}"
    )


def format_teaching_template(context: TeachingConsequenceContext) -> str:
    if context.unlinked:
        return (
            f"{context.name}未关联课次，暂无法评估补货对已排课次的影响。"
        )
    if context.expected_gap > 0:
        return (
            f"不补货将影响 {context.session_count} 个课次、"
            f"{context.headcount_total} 名学生的实训，"
            f"预计缺口 {context.expected_gap}{context.unit}。"
        )
    return (
        f"当前库存可覆盖 {context.session_count} 个课次、"
        f"{context.headcount_total} 名学生共 {context.expected_demand}"
        f"{context.unit} 的预计需求，暂不影响上课。"
    )


def _anchor_before(text: str, keywords: tuple[str, ...]) -> float | None:
    for keyword in keywords:
        match = re.search(
            r"(-?\d+(?:\.\d+)?)[^\d]{0,6}?" + re.escape(keyword), text
        )
        if match is not None:
            return float(match.group(1))
    return None


def validate_teaching_consequence(
    text: str, context: TeachingConsequenceContext
) -> bool:
    if context.unlinked:
        return TEACHING_UNLINKED_NOTE in text
    if not text or TEACHING_UNLINKED_NOTE in text:
        return False
    session_keywords = ("课次", "次课", "节课")
    if not any(keyword in text for keyword in session_keywords):
        return False
    allowed = {
        context.session_count,
        context.headcount_total,
        context.expected_demand,
        context.expected_gap,
    }
    numbers = [
        int(round(float(value)))
        for value in re.findall(r"\d+(?:\.\d+)?", text)
    ]
    if any(value not in allowed for value in numbers):
        return False
    count = _anchor_before(text, session_keywords) or _anchor_value(
        text, session_keywords
    )
    if count is None or int(round(count)) != context.session_count:
        return False
    if context.headcount_total > 0:
        headcount = _anchor_before(text, ("学生", "名", "人"))
        if headcount is None or int(round(headcount)) != context.headcount_total:
            return False
    if context.expected_gap > 0:
        gap = _anchor_value(text, ("缺口",))
        if gap is None or int(round(gap)) != context.expected_gap:
            return False
    return True


def build_teaching_prompt(context: TeachingConsequenceContext) -> str:
    if context.unlinked:
        return (
            f"耗材：{context.name}\n"
            f"关联情况：{TEACHING_UNLINKED_NOTE}\n"
            f"覆盖课次数：0\n"
            f"涉及学生人数：0\n"
            f"预计需求：0\n"
            f"预计缺口：0"
        )
    return (
        f"耗材：{context.name}\n"
        f"单位：{context.unit or '件'}\n"
        f"覆盖课次数：{context.session_count}\n"
        f"涉及学生人数：{context.headcount_total}\n"
        f"预计需求：{context.expected_demand}\n"
        f"预计缺口：{context.expected_gap}"
    )


class TemplateGenerator:

    def generate(self, context: RationaleContext) -> str:
        return format_template(context)


class DeepSeekRationaleGenerator:

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url

    @property
    def api_key(self) -> str:
        return self._api_key

    @property
    def model(self) -> str:
        return self._model

    @property
    def base_url(self) -> str:
        return self._base_url

    def _client(self) -> OpenAI:
        return OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=LLM_TIMEOUT_SECONDS,
            max_retries=0,
        )

    def generate(self, context: RationaleContext) -> str:
        client = self._client()
        response = client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(context)},
            ],
            temperature=0.3,
            timeout=LLM_TIMEOUT_SECONDS,
        )
        return (response.choices[0].message.content or "").strip()

    def generate_teaching(self, context: TeachingConsequenceContext) -> str:
        client = self._client()
        response = client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": TEACHING_SYSTEM_PROMPT},
                {"role": "user", "content": build_teaching_prompt(context)},
            ],
            temperature=0.3,
            timeout=LLM_TIMEOUT_SECONDS,
        )
        return (response.choices[0].message.content or "").strip()


def get_generator(settings: Settings | None = None) -> RationaleGenerator:
    config = settings if settings is not None else Settings()
    api_key = (config.llm_api_key or "").strip()
    if not api_key or api_key.startswith("sk-your"):
        return TemplateGenerator()
    return DeepSeekRationaleGenerator(
        api_key=api_key,
        model=config.llm_model or DEFAULT_MODEL,
        base_url=config.llm_base_url or DEFAULT_BASE_URL,
    )


def generate_rationale_result(context: RationaleContext) -> RationaleResult:
    started = time.perf_counter()
    generator = get_generator()
    if isinstance(generator, TemplateGenerator):
        return RationaleResult(format_template(context), RATIONALE_SOURCE_RULES, False)
    try:
        text = (generator.generate(context) or "").strip()
    except Exception:
        logger.warning(
            "%s 补货判断依据生成失败，回退规则模板（耗时 %.2fs）",
            log_tag(),
            time.perf_counter() - started,
            exc_info=True,
        )
        return RationaleResult(format_template(context), RATIONALE_SOURCE_RULES, True)
    if not text:
        logger.warning("%s 补货判断依据为空，回退规则模板", log_tag())
        return RationaleResult(format_template(context), RATIONALE_SOURCE_RULES, True)
    if not validate_rationale(text, context.evaluation):
        logger.warning("%s 补货判断依据与规则结果不一致，回退规则模板", log_tag())
        return RationaleResult(format_template(context), RATIONALE_SOURCE_RULES, True)
    logger.info(
        "%s 补货判断依据生成成功（%s，耗时 %.2fs）",
        log_tag(),
        type(generator).__name__,
        time.perf_counter() - started,
    )
    return RationaleResult(text, RATIONALE_SOURCE_LLM, False)


def generate_teaching_consequence_result(
    context: TeachingConsequenceContext,
) -> RationaleResult:
    template = format_teaching_template(context)
    if context.unlinked:
        return RationaleResult(template, RATIONALE_SOURCE_RULES, False)
    generator = get_generator()
    generate_teaching = getattr(generator, "generate_teaching", None)
    if generate_teaching is None:
        return RationaleResult(template, RATIONALE_SOURCE_RULES, False)
    started = time.perf_counter()
    try:
        text = (generate_teaching(context) or "").strip()
    except Exception:
        logger.warning(
            "%s 教学影响说明生成失败，回退规则模板（耗时 %.2fs）",
            log_tag(),
            time.perf_counter() - started,
            exc_info=True,
        )
        return RationaleResult(template, RATIONALE_SOURCE_RULES, True)
    if not text:
        logger.warning("%s 教学影响说明为空，回退规则模板", log_tag())
        return RationaleResult(template, RATIONALE_SOURCE_RULES, True)
    if not validate_teaching_consequence(text, context):
        logger.warning("%s 教学影响说明与结构化课次不一致，回退规则模板", log_tag())
        return RationaleResult(template, RATIONALE_SOURCE_RULES, True)
    return RationaleResult(text, RATIONALE_SOURCE_LLM, False)


def generate_rationale(context: RationaleContext) -> str:
    return generate_rationale_result(context).text
