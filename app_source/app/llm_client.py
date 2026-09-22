"""统一的 OpenAI 兼容大模型调用客户端。

特性：
- 基于 openai SDK：内置指数退避自动重试（瞬时 504/超时自愈）
- 多通道 failover：用户自定义通道 → 全局主通道 → 全局备用通道
- 备用通道由管理员在后台配置（app_config），适配中转站不稳定时切换官方直连
"""
import logging

from openai import OpenAI

from app.config import get_settings

logger = logging.getLogger("app.llm")

# SDK 自身重试次数（指数退避）；超时取 max(90s, settings)，给长 prompt 留足时间
SDK_MAX_RETRIES = 3


def _build_client(api_base: str, api_key: str) -> OpenAI:
    settings = get_settings()
    # 实测大请求（万级 token 输入 + 千级输出）可达 110 秒以上，超时给足 180s
    timeout = max(180.0, float(settings.llm_timeout_seconds))
    return OpenAI(base_url=api_base, api_key=api_key, timeout=timeout, max_retries=SDK_MAX_RETRIES)


def resolve_llm_channels(user=None) -> list[tuple[str, str, str]]:
    """按优先级返回可用通道列表 [(api_base, api_key, model), ...]。

    - 用户配置了专属通道：只使用用户通道（不回退全局，尊重用户隔离意图）
    - 否则：全局主通道 → 全局备用通道（后台已配置时）
    """
    from app import appconfig
    from app.security import decrypt_secret

    settings = get_settings()

    if user and getattr(user, "llm_api_base", None) and getattr(user, "llm_api_key", None):
        key = decrypt_secret(user.llm_api_key, settings.secret_key)
        if key and user.llm_api_base.strip():
            model = (user.llm_model or "").strip() or "gpt-5.6-luna"
            return [(user.llm_api_base.strip(), key, model)]
        return []

    channels: list[tuple[str, str, str]] = []
    base = str(appconfig.get_cfg("llm_api_base", settings.llm_api_base)).strip()
    key = decrypt_secret(str(appconfig.get_cfg("llm_api_key", settings.llm_api_key) or ""), settings.secret_key)
    model = str(appconfig.get_cfg("llm_model", settings.llm_model)).strip() or "gpt-5.6-luna"
    if base and key:
        channels.append((base, key, model))

    fbase = str(appconfig.get_cfg("llm_fallback_api_base", "")).strip()
    fkey = decrypt_secret(str(appconfig.get_cfg("llm_fallback_api_key", "")), settings.secret_key)
    fmodel = str(appconfig.get_cfg("llm_fallback_model", "")).strip() or model
    if fbase and fkey:
        channels.append((fbase, fkey, fmodel))

    return channels


def call_llm_chat(messages: list[dict], max_tokens: int = 10000, user=None, temperature: float = 0.3) -> str | None:
    """依次尝试各通道完成对话（采用 stream=True 实时接收，杜绝中转网关 504 超时），全部失败返回 None。"""
    channels = resolve_llm_channels(user)
    if not channels:
        logger.warning("未配置任何可用的 LLM 通道")
        return None

    last_err: Exception | None = None
    for api_base, api_key, model in channels:
        try:
            client = _build_client(api_base, api_key)
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )
            collected = []
            for chunk in response:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    collected.append(delta.content)
            content = "".join(collected).strip()
            if content:
                return content
            logger.warning("LLM 通道 %s (model=%s) 返回空内容", api_base, model)
            last_err = RuntimeError("empty content")
        except Exception as exc:
            last_err = exc
            logger.warning("LLM 通道 %s (model=%s) 调用失败: %s", api_base, model, exc)

    logger.error("所有 LLM 通道均失败: %s", last_err)
    return None
