from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import llm_crypto
from app.agent import llm as agent_llm
from app.database import get_db
from app.deps import CurrentUser, require_role
from app.models import LlmFeatureBinding, LlmPrompt, LlmProvider, User
from app.operation_log import record_operation
from app.schemas_llm import (
    BindingOut,
    BindingUpdate,
    ConnectionTestOut,
    ConnectionTestRequest,
    FeatureOut,
    PromptCreate,
    PromptOut,
    PromptUpdate,
    ProviderCreate,
    ProviderOut,
    ProviderUpdate,
)

router = APIRouter(
    prefix="/api/v1/llm",
    tags=["llm"],
    dependencies=[Depends(require_role("super_admin"))],
)

PROVIDER_NOT_FOUND = "供应商不存在"
PROMPT_NOT_FOUND = "提示词不存在"
BINDING_FEATURE_INVALID = "未知的功能标识"
PROMPT_FEATURE_MISMATCH = "提示词与功能不匹配"


def _provider_out(provider: LlmProvider) -> ProviderOut:
    return ProviderOut(
        id=provider.id,
        name=provider.name,
        base_url=provider.base_url,
        model=provider.model,
        timeout=provider.timeout,
        temperature=provider.temperature,
        enabled=provider.enabled,
        is_default=provider.is_default,
        supports_tools=provider.supports_tools,
        has_api_key=bool(provider.encrypted_api_key),
        api_key_masked=llm_crypto.masked_api_key(provider.encrypted_api_key),
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


def _binding_out(binding: LlmFeatureBinding) -> BindingOut:
    return BindingOut(
        id=binding.id,
        feature=binding.feature,
        provider_id=binding.provider_id,
        provider_name=binding.provider.name if binding.provider is not None else None,
        prompt_id=binding.prompt_id,
        prompt_version=binding.prompt.version if binding.prompt is not None else None,
        enabled=binding.enabled,
        updated_at=binding.updated_at,
    )


def _encrypt_or_error(api_key: str | None) -> str | None:
    if not api_key:
        return None
    if not llm_crypto.encryption_available():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=llm_crypto.MISSING_KEY_DETAIL,
        )
    return llm_crypto.encrypt_secret(api_key)


def _clear_default(db: Session, keep_id: int | None) -> None:
    stmt = update(LlmProvider).where(LlmProvider.is_default.is_(True))
    if keep_id is not None:
        stmt = stmt.where(LlmProvider.id != keep_id)
    db.execute(stmt.values(is_default=False))


def _get_provider(db: Session, provider_id: int) -> LlmProvider:
    provider = db.get(LlmProvider, provider_id)
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=PROVIDER_NOT_FOUND)
    return provider


def _provider_snapshot(provider: LlmProvider) -> str:
    return (
        f'{{"id":{provider.id},"name":"{provider.name}","model":"{provider.model}",'
        f'"enabled":{str(provider.enabled).lower()},"is_default":{str(provider.is_default).lower()}}}'
    )


def _prompt_snapshot(prompt: LlmPrompt) -> str:
    return (
        f'{{"id":{prompt.id},"feature":"{prompt.feature}","version":{prompt.version},'
        f'"active":{str(prompt.active).lower()},"enabled":{str(prompt.enabled).lower()}}}'
    )


@router.get("/features")
def list_features() -> list[FeatureOut]:
    return [FeatureOut(**spec) for spec in agent_llm.FEATURE_SPECS]


@router.get("/providers")
def list_providers(db: Annotated[Session, Depends(get_db)]) -> list[ProviderOut]:
    rows = db.scalars(
        select(LlmProvider).order_by(LlmProvider.is_default.desc(), LlmProvider.id)
    ).all()
    return [_provider_out(row) for row in rows]


@router.post("/providers", status_code=status.HTTP_201_CREATED)
def create_provider(
    payload: ProviderCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> ProviderOut:
    existing = db.scalars(select(LlmProvider).where(LlmProvider.name == payload.name)).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"供应商名称 {payload.name} 已存在",
        )
    encrypted = _encrypt_or_error(payload.api_key)
    if payload.is_default:
        _clear_default(db, None)
    provider = LlmProvider(
        name=payload.name,
        base_url=payload.base_url,
        model=payload.model,
        encrypted_api_key=encrypted,
        timeout=payload.timeout,
        temperature=payload.temperature,
        enabled=payload.enabled,
        is_default=payload.is_default,
        supports_tools=payload.supports_tools,
    )
    db.add(provider)
    try:
        db.flush()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="供应商名称已存在"
        ) from error
    record_operation(
        db,
        action="llm_provider_create",
        operator=current_user,
        target_type="llm_provider",
        target_id=provider.id,
        target_label=provider.name,
        after_value=_provider_snapshot(provider),
        remark=f"创建 LLM 供应商 {provider.name}",
    )
    db.commit()
    db.refresh(provider)
    return _provider_out(provider)


@router.patch("/providers/{provider_id}")
def update_provider(
    provider_id: int,
    payload: ProviderUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> ProviderOut:
    provider = _get_provider(db, provider_id)
    before = _provider_snapshot(provider)
    changed = False
    if payload.name is not None and payload.name != provider.name:
        conflict = db.scalars(
            select(LlmProvider).where(
                LlmProvider.name == payload.name, LlmProvider.id != provider_id
            )
        ).first()
        if conflict is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"供应商名称 {payload.name} 已存在",
            )
        provider.name = payload.name
        changed = True
    for field in ("base_url", "model", "timeout", "temperature", "enabled", "supports_tools"):
        value = getattr(payload, field)
        if value is not None and value != getattr(provider, field):
            setattr(provider, field, value)
            changed = True
    if payload.api_key:
        provider.encrypted_api_key = _encrypt_or_error(payload.api_key)
        changed = True
    if payload.is_default is not None and payload.is_default != provider.is_default:
        if payload.is_default:
            _clear_default(db, provider.id)
        provider.is_default = payload.is_default
        changed = True
    if not changed:
        return _provider_out(provider)
    provider.updated_at = datetime.now()
    record_operation(
        db,
        action="llm_provider_update",
        operator=current_user,
        target_type="llm_provider",
        target_id=provider.id,
        target_label=provider.name,
        before_value=before,
        after_value=_provider_snapshot(provider),
        remark=f"更新 LLM 供应商 {provider.name}",
    )
    db.commit()
    db.refresh(provider)
    return _provider_out(provider)


@router.delete("/providers/{provider_id}")
def delete_provider(
    provider_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> dict[str, str]:
    provider = _get_provider(db, provider_id)
    before = _provider_snapshot(provider)
    db.execute(
        update(LlmFeatureBinding)
        .where(LlmFeatureBinding.provider_id == provider_id)
        .values(provider_id=None)
    )
    name = provider.name
    db.delete(provider)
    record_operation(
        db,
        action="llm_provider_delete",
        operator=current_user,
        target_type="llm_provider",
        target_id=provider_id,
        target_label=name,
        before_value=before,
        remark=f"删除 LLM 供应商 {name}",
    )
    db.commit()
    return {"status": "ok"}


@router.post("/providers/test")
def test_provider_payload(
    payload: ProviderCreate,
    current_user: CurrentUser,
) -> ConnectionTestOut:
    if not payload.api_key:
        return ConnectionTestOut(ok=False, message="请填写 API Key 后再测试", model=payload.model)
    ok, message, latency = agent_llm.test_connection(
        payload.base_url, payload.api_key, payload.model, payload.timeout
    )
    return ConnectionTestOut(ok=ok, message=message, latency_ms=latency, model=payload.model)


@router.post("/providers/{provider_id}/test")
def test_provider(
    provider_id: int,
    payload: ConnectionTestRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> ConnectionTestOut:
    provider = _get_provider(db, provider_id)
    api_key = (payload.api_key or "").strip() or llm_crypto.decrypt_secret(
        provider.encrypted_api_key
    )
    if not api_key:
        return ConnectionTestOut(
            ok=False, message="供应商未配置可用 API Key", model=provider.model
        )
    ok, message, latency = agent_llm.test_connection(
        provider.base_url,
        api_key,
        provider.model,
        provider.timeout,
    )
    record_operation(
        db,
        action="llm_provider_test",
        operator=current_user,
        target_type="llm_provider",
        target_id=provider.id,
        target_label=provider.name,
        result="success" if ok else "failure",
        remark=f"测试连接 {'成功' if ok else '失败'}：{provider.name}",
    )
    db.commit()
    return ConnectionTestOut(ok=ok, message=message, latency_ms=latency, model=provider.model)


@router.get("/prompts")
def list_prompts(
    db: Annotated[Session, Depends(get_db)],
    feature: str | None = None,
) -> list[PromptOut]:
    stmt = select(LlmPrompt)
    if feature:
        stmt = stmt.where(LlmPrompt.feature == feature)
    rows = db.scalars(stmt.order_by(LlmPrompt.feature, LlmPrompt.version.desc())).all()
    return [PromptOut.model_validate(row, from_attributes=True) for row in rows]


@router.post("/prompts", status_code=status.HTTP_201_CREATED)
def create_prompt(
    payload: PromptCreate,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> PromptOut:
    if not agent_llm.is_known_feature(payload.feature):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=BINDING_FEATURE_INVALID
        )
    latest = db.scalars(
        select(func.max(LlmPrompt.version)).where(LlmPrompt.feature == payload.feature)
    ).first()
    version = (latest or 0) + 1
    has_active = db.scalars(
        select(LlmPrompt.id).where(
            LlmPrompt.feature == payload.feature, LlmPrompt.active.is_(True)
        )
    ).first()
    active = payload.active or has_active is None
    prompt = LlmPrompt(
        feature=payload.feature,
        version=version,
        system_prompt=payload.system_prompt.strip(),
        enabled=payload.enabled,
        active=active,
        created_by=current_user.account,
    )
    db.add(prompt)
    db.flush()
    if active:
        db.execute(
            update(LlmPrompt)
            .where(
                LlmPrompt.feature == payload.feature,
                LlmPrompt.id != prompt.id,
            )
            .values(active=False)
        )
    record_operation(
        db,
        action="llm_prompt_create",
        operator=current_user,
        target_type="llm_prompt",
        target_id=prompt.id,
        target_label=f"{prompt.feature} v{prompt.version}",
        after_value=_prompt_snapshot(prompt),
        remark=f"新建提示词 {prompt.feature} v{prompt.version}",
    )
    db.commit()
    db.refresh(prompt)
    return PromptOut.model_validate(prompt, from_attributes=True)


@router.patch("/prompts/{prompt_id}")
def update_prompt(
    prompt_id: int,
    payload: PromptUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> PromptOut:
    prompt = db.get(LlmPrompt, prompt_id)
    if prompt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=PROMPT_NOT_FOUND)
    before = _prompt_snapshot(prompt)
    if payload.system_prompt is not None:
        prompt.system_prompt = payload.system_prompt.strip()
    if payload.enabled is not None:
        prompt.enabled = payload.enabled
        if not payload.enabled:
            prompt.active = False
    record_operation(
        db,
        action="llm_prompt_update",
        operator=current_user,
        target_type="llm_prompt",
        target_id=prompt.id,
        target_label=f"{prompt.feature} v{prompt.version}",
        before_value=before,
        after_value=_prompt_snapshot(prompt),
        remark=f"更新提示词 {prompt.feature} v{prompt.version}",
    )
    db.commit()
    db.refresh(prompt)
    return PromptOut.model_validate(prompt, from_attributes=True)


def _activate_prompt(db: Session, prompt: LlmPrompt, current_user: User) -> PromptOut:
    db.execute(
        update(LlmPrompt)
        .where(LlmPrompt.feature == prompt.feature, LlmPrompt.id != prompt.id)
        .values(active=False)
    )
    prompt.active = True
    prompt.enabled = True
    record_operation(
        db,
        action="llm_prompt_activate",
        operator=current_user,
        target_type="llm_prompt",
        target_id=prompt.id,
        target_label=f"{prompt.feature} v{prompt.version}",
        after_value=_prompt_snapshot(prompt),
        remark=f"激活提示词 {prompt.feature} v{prompt.version}",
    )
    db.commit()
    db.refresh(prompt)
    return PromptOut.model_validate(prompt, from_attributes=True)


@router.post("/prompts/{prompt_id}/activate")
def activate_prompt(
    prompt_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> PromptOut:
    prompt = db.get(LlmPrompt, prompt_id)
    if prompt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=PROMPT_NOT_FOUND)
    return _activate_prompt(db, prompt, current_user)


@router.post("/prompts/{prompt_id}/rollback")
def rollback_prompt(
    prompt_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> PromptOut:
    prompt = db.get(LlmPrompt, prompt_id)
    if prompt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=PROMPT_NOT_FOUND)
    return _activate_prompt(db, prompt, current_user)


@router.get("/bindings")
def list_bindings(db: Annotated[Session, Depends(get_db)]) -> list[BindingOut]:
    rows = db.scalars(select(LlmFeatureBinding).order_by(LlmFeatureBinding.feature)).all()
    existing = {row.feature: row for row in rows}
    result: list[BindingOut] = []
    for key in agent_llm.FEATURE_KEYS:
        binding = existing.get(key)
        if binding is not None:
            result.append(_binding_out(binding))
        else:
            result.append(
                BindingOut(
                    id=0,
                    feature=key,
                    provider_id=None,
                    provider_name=None,
                    prompt_id=None,
                    prompt_version=None,
                    enabled=False,
                    updated_at=datetime.now(),
                )
            )
    return result


@router.put("/bindings/{feature}")
def update_binding(
    feature: str,
    payload: BindingUpdate,
    db: Annotated[Session, Depends(get_db)],
    current_user: CurrentUser,
) -> BindingOut:
    if not agent_llm.is_known_feature(feature):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=BINDING_FEATURE_INVALID
        )
    if payload.provider_id is not None:
        _get_provider(db, payload.provider_id)
    if payload.prompt_id is not None:
        prompt = db.get(LlmPrompt, payload.prompt_id)
        if prompt is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=PROMPT_NOT_FOUND
            )
        if prompt.feature != feature:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=PROMPT_FEATURE_MISMATCH
            )
    binding = db.scalars(
        select(LlmFeatureBinding).where(LlmFeatureBinding.feature == feature)
    ).first()
    if binding is None:
        binding = LlmFeatureBinding(feature=feature)
        db.add(binding)
    binding.provider_id = payload.provider_id
    binding.prompt_id = payload.prompt_id
    binding.enabled = payload.enabled
    binding.updated_at = datetime.now()
    db.flush()
    record_operation(
        db,
        action="llm_binding_update",
        operator=current_user,
        target_type="llm_binding",
        target_id=binding.id,
        target_label=feature,
        after_value=(
            f'{{"feature":"{feature}","provider_id":{payload.provider_id},'
            f'"prompt_id":{payload.prompt_id},"enabled":{str(payload.enabled).lower()}}}'
        ),
        remark=f"更新功能绑定 {feature}",
    )
    db.commit()
    db.refresh(binding)
    return _binding_out(binding)
