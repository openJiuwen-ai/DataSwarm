#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""llm_client.py —— skill 侧 LLM 调用公共封装（巡检 LLM 集成方案 §2.2）。

供 ai_interpret（阶段一解读）、business 规则生成（阶段二）、根因假设排序
（阶段三）复用。照 advanced-daily-report/analyzers/ai_analyzer.py 先例，
抽取为通用函数，差异：
    - 配置多一级 fallback：ai_analysis > react.model_client_config >
      models.defaults 的 is_default 条目/首位（当前真实配置在 models.defaults，
      见 ~/.dataswarm/config/config.yaml）
    - 值中的 ``${VAR}`` 占位符按 ``resolve_env_vars`` 语义展开（真值在
      config/.env），与对话侧配置解析一致
    - call_llm 带硬超时（先例无超时控制）
    - 模块不可导入（LLM_AVAILABLE=False）或 api_key 为空 → 调用方拿到空串走降级

契约：call_llm 失败返回空串，永不抛异常——调用方判空降级即可。
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

import dw_paths

_ENV_VAR_RE = re.compile(r"\$\{([^:}]+)(?::-([^}]*))?\}")


def _resolve_env_vars(value):
    """展开 ``${VAR}`` / ``${VAR:-default}``（与基座 ``resolve_env_vars`` 同语义）。

    发布模板的 models.defaults 是占位符，真值在 config/.env（服务进程已加载进
    os.environ）；裸读不展开会把字面量发给网关——exe 首装形态下对话正常、
    本链路必失败。skill 脚本不依赖 jiuwenswarm 包，故就近实现。
    """
    if isinstance(value, str):
        def _replace(match):
            name, default = match.group(1), match.group(2)
            current = os.getenv(name)
            if default is not None:
                return default if current in (None, "") else current
            return current if current is not None else ""
        return _ENV_VAR_RE.sub(_replace, value)
    if isinstance(value, dict):
        return {key: _resolve_env_vars(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(item) for item in value]
    return value

# 尝试导入 LLM 相关模块（照 ai_analyzer 先例）
try:
    from openjiuwen.core.foundation.llm import (  # type: ignore
        Model,
        ModelClientConfig,
        ModelRequestConfig,
        SystemMessage,
        UserMessage,
    )

    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False
    Model = None  # type: ignore[assignment]

try:
    import yaml

    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


def _default_config_path() -> Path:
    """默认配置路径：workspace_root()/config/config.yaml（env 优先）。"""
    return dw_paths.llm_config_path()


def load_llm_config(config_path: str | None = None) -> dict:
    """多级 fallback 读取 LLM 配置。

    Args:
        config_path: 显式配置文件路径；None 时用 ~/.dataswarm/config/config.yaml
    Returns:
        {model_name, api_base, api_key, verify_ssl}；读不到的键给默认值。
        api_key 可能为空串——调用方须判空降级。
    """
    config: dict = {
        "model_name": "glm-4.7",
        "api_base": "https://open.bigmodel.cn/api/paas/v4",
        "api_key": "",
        "verify_ssl": True,
    }
    path = Path(config_path) if config_path else _default_config_path()
    if not path.is_file():
        return config
    if not YAML_AVAILABLE:
        return config
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return config
    if not isinstance(raw, dict):
        return config

    # 段优先级: ai_analysis > react.model_client_config > models.defaults[0]
    # react 段必须带 model_client_config 才生效——当前真实 config 的 react 段只有
    # model_name（env 占位符）无连接配置，直接进 react 分支会拿到空配置错过真实凭据。
    candidate: dict | None = None
    if isinstance(raw.get("ai_analysis"), dict):
        cand = raw["ai_analysis"]
        candidate = {
            "model_name": cand.get("model_name"),
            "api_base": cand.get("api_base"),
            "api_key": cand.get("api_key"),
        }
    elif isinstance(raw.get("react"), dict) and isinstance(
            raw["react"].get("model_client_config"), dict):
        cand = raw["react"]["model_client_config"]
        candidate = {
            "model_name": raw["react"].get("model_name") or cand.get("model_name"),
            "api_base": cand.get("api_base"),
            "api_key": cand.get("api_key"),
        }
    else:
        defaults = raw.get("models")
        if isinstance(defaults, dict):
            defaults = defaults.get("defaults")
        if isinstance(defaults, list) and defaults:
            # 对话主模型 = is_default=True 的条目（不一定是首位）
            entry = next((item for item in defaults
                          if isinstance(item, dict) and item.get("is_default")), defaults[0])
            cand = entry.get("model_client_config") or {}
            candidate = {
                "model_name": cand.get("model_name"),
                "api_base": cand.get("api_base"),
                "api_key": cand.get("api_key"),
                "verify_ssl": cand.get("verify_ssl"),
            }
    if candidate:
        candidate = _resolve_env_vars(candidate)
        for key in ("model_name", "api_base", "api_key", "verify_ssl"):
            val = candidate.get(key)
            if val is not None:
                config[key] = val
    return config


class _LLMClient:
    """惰性持有 Model 实例（照 ai_analyzer 的 _get_model 模式）。"""

    def __init__(self, config_path: str | None = None):
        self.config = load_llm_config(config_path)
        self._model = None

    def _get_model(self):
        if self._model is None:
            if not LLM_AVAILABLE:
                raise RuntimeError("LLM 模块未安装，请检查 openjiuwen 包")
            if not self.config.get("api_key"):
                raise RuntimeError("未配置 api_key（config.yaml 见 SKILL.md 说明）")
            client_config = ModelClientConfig(
                client_provider="OpenAI",
                api_base=self.config["api_base"],
                api_key=self.config["api_key"],
                verify_ssl=self.config.get("verify_ssl", True),
                # 独享 http client：openjiuwen 默认共享池绑定首个事件循环，
                # 同进程其他 asyncio.run（如 finding_review 的并发 gather）关闭
                # loop 后共享池失效，后续调用报 Connection error（实测复现）。
                # skill 进程即起即退，独享连接代价可忽略。
                use_shared_llm_http_client=False,
            )
            model_config = ModelRequestConfig(model=self.config["model_name"])
            self._model = Model(
                model_client_config=client_config,
                model_config=model_config,
            )
        return self._model

    async def call(self, system_prompt: str, user_prompt: str,
                   temperature: float = 0.3, timeout_s: int = 90) -> str:
        model = self._get_model()
        messages = [
            SystemMessage(content=system_prompt),
            UserMessage(content=user_prompt),
        ]
        # temperature/timeout 必须经 invoke 覆盖传给模型——ModelRequestConfig 的
        # 默认 temperature=0.95 会生效（实测 metadata 里 0.95），违背「解读要稳定」约束
        response = await model.invoke(messages=messages,
                                      temperature=temperature,
                                      timeout=timeout_s)
        return response.content if hasattr(response, "content") else str(response)


_CLIENT: _LLMClient | None = None


async def call_llm(system_prompt: str, user_prompt: str, *,
                    config_path: str | None = None,
                    temperature: float = 0.3,
                    timeout_s: int = 90) -> str:
    """调 LLM，失败/超时/未配置返回空串（不抛异常）。

    Args:
        system_prompt: 系统提示词
        user_prompt: 用户提示词
        config_path: 显式配置路径；None 时用默认路径
        temperature: 低——解读任务要稳定不要创意
        timeout_s: 硬超时上限
    Returns:
        LLM 响应文本；失败时空串，调用方判空降级
    """
    global _CLIENT
    try:
        if _CLIENT is None or config_path is not None:
            _CLIENT = _LLMClient(config_path)
        coro = _CLIENT.call(system_prompt, user_prompt,
                            temperature=temperature, timeout_s=timeout_s)
        return await asyncio.wait_for(coro, timeout=timeout_s + 5)
    except Exception as exc:  # noqa: BLE001 —— 降级契约：永不抛
        print(f"[llm_client][warn] LLM 调用失败: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return ""


def call_llm_sync(system_prompt: str, user_prompt: str, *,
                  config_path: str | None = None,
                  temperature: float = 0.3,
                  timeout_s: int = 90) -> str:
    """call_llm 的同步包装（asyncio.run）。参数语义同 call_llm。"""
    return asyncio.run(call_llm(system_prompt, user_prompt,
                                 config_path=config_path,
                                 temperature=temperature,
                                 timeout_s=timeout_s))


# runtime 路径下 _PROJECT_ROOT 失效的陷阱见 memory：skill-runtime-path-trap。
# 本模块不依赖工程根路径，配置只从显式路径或 ~/.dataswarm 解析，
# 无需就近 _PROJECT_ROOT 探测。
