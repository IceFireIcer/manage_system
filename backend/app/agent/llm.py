import logging
import re
import time
from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI

from app.agent.rules import Evaluation
from app.config import Settings, get_settings
from app.request_context import log_tag

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
LLM_TIMEOUT_SECONDS = 3.0

RATIONALE_SOURCE_LLM = "llm"
RATIONALE_SOURCE_RULES = "rules"

FEATURE_RESTOCK_RATIONALE = "restock_rationale"
FEATURE_TEACHING_CONSEQUENCE = "teaching_consequence"
FEATURE_ASSISTANT = "assistant"

FEATURE_SPECS: tuple[dict[str, str], ...] = (
    {
        "key": FEATURE_RESTOCK_RATIONALE,
        "label": "补货理由",
        "description": "补货建议的判断依据说明",
    },
    {
        "key": FEATURE_TEACHING_CONSEQUENCE,
        "label": "教学后果",
        "description": "不补货对已排课次的影响说明",
    },
    {
        "key": FEATURE_ASSISTANT,
        "label": "悬浮助手",
        "description": "悬浮助手的系统提示词",
    },
)
FEATURE_KEYS: tuple[str, ...] = tuple(spec["key"] for spec in FEATURE_SPECS)
FEATURE_LABELS: dict[str, str] = {spec["key"]: spec["label"] for spec in FEATURE_SPECS}


def is_known_feature(feature: str) -> bool:
    return feature in FEATURE_LABELS

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
        timeout: float = LLM_TIMEOUT_SECONDS,
        temperature: float = 0.3,
        system_prompt: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._timeout = timeout if timeout and timeout > 0 else LLM_TIMEOUT_SECONDS
        self._temperature = temperature
        self._system_prompt = system_prompt

    @property
    def api_key(self) -> str:
        return self._api_key

    @property
    def model(self) -> str:
        return self._model

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def timeout(self) -> float:
        return self._timeout

    @property
    def temperature(self) -> float:
        return self._temperature

    @property
    def system_prompt(self) -> str | None:
        return self._system_prompt

    def _client(self) -> OpenAI:
        return OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout,
            max_retries=0,
        )

    def _rationale_prompt(self) -> str:
        return self._system_prompt or SYSTEM_PROMPT

    def _teaching_prompt(self) -> str:
        return self._system_prompt or TEACHING_SYSTEM_PROMPT

    def generate(self, context: RationaleContext) -> str:
        client = self._client()
        response = client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": self._rationale_prompt()},
                {"role": "user", "content": build_prompt(context)},
            ],
            temperature=self._temperature,
            timeout=self._timeout,
        )
        return (response.choices[0].message.content or "").strip()

    def generate_teaching(self, context: TeachingConsequenceContext) -> str:
        client = self._client()
        response = client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": self._teaching_prompt()},
                {"role": "user", "content": build_teaching_prompt(context)},
            ],
            temperature=self._temperature,
            timeout=self._timeout,
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


def _provider_from_db(db, feature: str):
    """按功能绑定或默认供应商解析可用供应商；异常时返回 None 交由 env 回退。"""
    from sqlalchemy import select

    from app.models import LlmFeatureBinding, LlmProvider

    binding = db.scalars(
        select(LlmFeatureBinding).where(
            LlmFeatureBinding.feature == feature,
            LlmFeatureBinding.enabled.is_(True),
        )
    ).first()
    provider = None
    prompt = None
    if binding is not None:
        if binding.provider is not None and binding.provider.enabled:
            provider = binding.provider
        if binding.prompt is not None and binding.prompt.enabled:
            prompt = binding.prompt
    if provider is None:
        provider = db.scalars(
            select(LlmProvider).where(
                LlmProvider.is_default.is_(True),
                LlmProvider.enabled.is_(True),
            )
        ).first()
    return provider, prompt


def resolve_generator(db, feature: str, settings: Settings | None = None) -> RationaleGenerator:
    """优先 DB 绑定，异常或未配置回退 env，再回退规则模板。"""
    from app import llm_crypto

    try:
        provider, prompt = _provider_from_db(db, feature)
    except Exception:
        logger.warning("%s 读取 LLM 功能绑定失败，回退环境变量", log_tag(), exc_info=True)
        return get_generator(settings)
    if provider is None:
        return get_generator(settings)
    api_key = llm_crypto.decrypt_secret(provider.encrypted_api_key)
    if not api_key:
        logger.warning(
            "%s 供应商 %s 的 API Key 缺失或无法解密，回退环境变量",
            log_tag(),
            provider.name,
        )
        return get_generator(settings)
    return DeepSeekRationaleGenerator(
        api_key=api_key,
        model=provider.model or DEFAULT_MODEL,
        base_url=provider.base_url or DEFAULT_BASE_URL,
        timeout=provider.timeout or LLM_TIMEOUT_SECONDS,
        temperature=provider.temperature,
        system_prompt=prompt.system_prompt if prompt is not None else None,
    )


def test_connection(
    base_url: str,
    api_key: str,
    model: str,
    timeout: float = 10.0,
) -> tuple[bool, str, int | None]:
    """发送最小请求验证供应商连通性；返回 (是否成功, 消息, 耗时毫秒)。"""
    started = time.perf_counter()
    try:
        client = OpenAI(
            api_key=api_key,
            base_url=base_url or DEFAULT_BASE_URL,
            timeout=timeout if timeout and timeout > 0 else 10.0,
            max_retries=0,
        )
        client.chat.completions.create(
            model=model or DEFAULT_MODEL,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            temperature=0,
        )
    except Exception as error:  # noqa: BLE001 - 测试连接需回显任意异常
        latency = int((time.perf_counter() - started) * 1000)
        return False, str(error)[:200] or "连接失败", latency
    latency = int((time.perf_counter() - started) * 1000)
    return True, "连接成功", latency


def generate_rationale_result(
    context: RationaleContext,
    generator: RationaleGenerator | None = None,
) -> RationaleResult:
    started = time.perf_counter()
    generator = generator if generator is not None else get_generator()
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
    generator: RationaleGenerator | None = None,
) -> RationaleResult:
    template = format_teaching_template(context)
    if context.unlinked:
        return RationaleResult(template, RATIONALE_SOURCE_RULES, False)
    generator = generator if generator is not None else get_generator()
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


def generate_rationale(
    context: RationaleContext,
    generator: RationaleGenerator | None = None,
) -> str:
    return generate_rationale_result(context, generator).text


ASSISTANT_DEFAULT_SYSTEM_PROMPT = (
    "你是班级实训耗材管理系统的只读问答助手。"
    "你只能依据给出的系统上下文与数据回答问题，不得编造未提供的数据，"
    "不得输出密码、API Key、学生姓名/学号/手机号/邮箱等个人敏感信息。"
    "回答使用简体中文，可使用 Markdown 列表与加粗。"
    "你无法修改库存或任何业务数据，只能给出只读分析与建议。"
)


@dataclass(frozen=True)
class ChatConfig:
    api_key: str
    model: str
    base_url: str
    timeout: float
    temperature: float
    system_prompt: str | None
    supports_tools: bool = False


def resolve_chat_config(
    db, feature: str, settings: Settings | None = None
) -> ChatConfig | None:
    """解析可用的聊天配置：优先 DB 功能绑定，回退环境变量，均无则返回 None。"""
    from app import llm_crypto

    provider = None
    prompt = None
    try:
        provider, prompt = _provider_from_db(db, feature)
    except Exception:
        logger.warning("%s 读取助手 LLM 绑定失败，回退环境变量", log_tag(), exc_info=True)
        provider = None
        prompt = None
    if provider is not None:
        api_key = llm_crypto.decrypt_secret(provider.encrypted_api_key)
        if api_key:
            system_prompt = (
                prompt.system_prompt
                if prompt is not None and prompt.enabled
                else None
            )
            return ChatConfig(
                api_key=api_key,
                model=provider.model or DEFAULT_MODEL,
                base_url=provider.base_url or DEFAULT_BASE_URL,
                timeout=provider.timeout or LLM_TIMEOUT_SECONDS,
                temperature=provider.temperature,
                system_prompt=system_prompt,
                supports_tools=bool(provider.supports_tools),
            )
    config = settings if settings is not None else get_settings()
    api_key = (config.llm_api_key or "").strip()
    if api_key and not api_key.startswith("sk-your"):
        return ChatConfig(
            api_key=api_key,
            model=config.llm_model or DEFAULT_MODEL,
            base_url=config.llm_base_url or DEFAULT_BASE_URL,
            timeout=LLM_TIMEOUT_SECONDS,
            temperature=0.3,
            system_prompt=None,
            supports_tools=False,
        )
    return None


def run_chat(
    config: ChatConfig,
    messages: list[dict],
    *,
    timeout: float | None = None,
) -> str:
    """向聊天模型发送消息，返回文本回复；异常交由调用方处理。"""
    client = OpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=timeout or config.timeout,
        max_retries=0,
    )
    response = client.chat.completions.create(
        model=config.model,
        messages=messages,
        temperature=config.temperature,
        timeout=timeout or config.timeout,
    )
    return (response.choices[0].message.content or "").strip()
