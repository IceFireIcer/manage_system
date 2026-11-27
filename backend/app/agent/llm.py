import logging
import time
from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI

from app.agent.rules import Evaluation
from app.config import Settings

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "deepseek-chat"
DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
LLM_TIMEOUT_SECONDS = 3.0

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


@dataclass(frozen=True)
class RationaleContext:
    name: str
    unit: str
    evaluation: Evaluation


class RationaleGenerator(Protocol):
    def generate(self, context: RationaleContext) -> str: ...


def format_template(context: RationaleContext) -> str:
    evaluation = context.evaluation
    unit = context.unit
    if evaluation.q7 == 0:
        consumption = "近 7 天无领用流水"
    else:
        consumption = (
            f"近 7 天净消耗 {evaluation.q7}{unit}"
            f"（日均 {evaluation.rate:.2f}{unit}）"
        )

    if evaluation.current_stock >= evaluation.safety_stock:
        return (
            f"{context.name}当前库存 {evaluation.current_stock}{unit}，不低于安全库存线 "
            f"{evaluation.safety_stock}{unit}；{consumption}，较前 7 天 "
            f"{evaluation.prev_q7}{unit} 明显上升，按一周用量建议补货 "
            f"{evaluation.recommended_quantity}{unit}。"
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
        f"{summary}，{TIER_KEYWORDS[evaluation.tier]} "
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

    def generate(self, context: RationaleContext) -> str:
        client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=LLM_TIMEOUT_SECONDS,
            max_retries=0,
        )
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


def generate_rationale(context: RationaleContext) -> str:
    started = time.perf_counter()
    generator = get_generator()
    try:
        text = (generator.generate(context) or "").strip()
    except Exception:
        logger.warning(
            "补货判断依据生成失败，回退规则模板（耗时 %.2fs）",
            time.perf_counter() - started,
            exc_info=True,
        )
        return format_template(context)
    if not text:
        logger.warning("补货判断依据为空，回退规则模板")
        return format_template(context)
    logger.info(
        "补货判断依据生成成功（%s，耗时 %.2fs）",
        type(generator).__name__,
        time.perf_counter() - started,
    )
    return text
