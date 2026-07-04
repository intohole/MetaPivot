"""OODA 自愈执行器 - 工具调用失败时的分类、重试与 fallback

OODA 循环：
- Observe：捕获工具执行结果（成功/失败 + 错误信息）
- Orient：FailureClassifier 分类失败类型（transient/permanent/permission/unknown）
- Decide：RetryPolicy 决定重试策略（transient/unknown 重试，permanent/permission fallback）
- Act：执行重试（指数退避）或 fallback（查找替代工具）

设计：
- 仅 transient/unknown 重试（避免对永久性错误无谓重试）
- 重试耗尽后调 _try_fallback 查找替代工具
- 成功后 _prune_failed_history 清理失败历史（防上下文污染）
"""
import asyncio
import json
import re
from datetime import datetime
from typing import Any, Optional

from app.domain.agent.state import AgentState, StepRecord
from app.utils.logger import get_logger

log = get_logger("agent_healer")


class FailureClassifier:
    """失败类型分类器（基于正则匹配错误信息）"""

    TRANSIENT = re.compile(
        r"timeout|timed out|temporarily|503|502|504|connection|reset|"
        r"broken pipe|eof|retry|unavailable|overloaded",
        re.IGNORECASE,
    )
    PERMANENT = re.compile(
        r"not found|invalid|forbidden|400|404|422|schema|argument|"
        r"unsupported|unknown tool|does not exist",
        re.IGNORECASE,
    )
    PERMISSION = re.compile(
        r"permission|denied|unauthorized|401|403|forbidden access|"
        r"not allowed|insufficient",
        re.IGNORECASE,
    )

    @classmethod
    def classify(cls, error: str) -> str:
        """分类失败类型

        Returns:
            transient / permanent / permission / unknown
        """
        if not error:
            return "unknown"
        if cls.PERMISSION.search(error):
            return "permission"
        if cls.PERMANENT.search(error):
            return "permanent"
        if cls.TRANSIENT.search(error):
            return "transient"
        return "unknown"


class RetryPolicy:
    """重试策略（指数退避）"""

    MAX_RETRIES = 3
    BASE_DELAY = 1.0  # 基础延迟（秒）
    MAX_DELAY = 30.0  # 最大延迟（秒）

    @classmethod
    def should_retry(cls, failure_type: str, retry_count: int) -> bool:
        """判断是否应重试"""
        if retry_count >= cls.MAX_RETRIES:
            return False
        # 仅 transient/unknown 重试，permanent/permission 立即 fallback
        return failure_type in ("transient", "unknown")

    @classmethod
    def get_delay(cls, retry_count: int) -> float:
        """计算指数退避延迟"""
        delay = cls.BASE_DELAY * (2 ** (retry_count - 1))
        return min(delay, cls.MAX_DELAY)


class Healer:
    """OODA 自愈执行器实现"""

    async def execute_with_healing(
        self,
        state: AgentState,
        tc: Any,
        executor_fn: Any,
    ) -> StepRecord:
        """带自愈的工具调用执行

        Args:
            state: AgentState 实例
            tc: tool_call 对象
            executor_fn: execute_tool_call 函数（单次执行，无重试）

        Returns:
            StepRecord — 成功或 fallback 后的步骤记录
        """
        tool_name = tc.function.name if hasattr(tc, "function") else "unknown"
        retry_count = 0

        while True:
            # Observe: 执行工具调用
            step = await executor_fn(state, tc)

            # 成功或非失败状态直接返回
            if step.status != "failed":
                if retry_count > 0:
                    step.tool_output = step.tool_output or {}
                    step.tool_output["retried"] = retry_count
                    state.add_event("tool_retry", {
                        "tool": tool_name, "retries": retry_count, "result": "success",
                    })
                    # Act: 清理失败历史（防上下文污染）
                    _prune_failed_history(state, tool_name)
                return step

            # Orient: 分类失败
            error_msg = step.error or str(step.tool_output or "")
            failure_type = FailureClassifier.classify(error_msg)

            # Decide: 是否重试
            retry_count += 1
            if RetryPolicy.should_retry(failure_type, retry_count):
                delay = RetryPolicy.get_delay(retry_count)
                log.info(
                    "Tool {} failed ({}), retry {}/{} after {:.1f}s",
                    tool_name, failure_type, retry_count, RetryPolicy.MAX_RETRIES, delay,
                )
                state.add_event("tool_retry", {
                    "tool": tool_name, "retry": retry_count,
                    "failure_type": failure_type, "delay": delay,
                })
                await asyncio.sleep(delay)
                continue

            # 重试耗尽或不可重试 → 尝试 fallback
            log.warning(
                "Tool {} failed ({}, retries={}), trying fallback",
                tool_name, failure_type, retry_count,
            )
            return await self._try_fallback(state, tc, step, failure_type)

    async def _try_fallback(
        self,
        state: AgentState,
        tc: Any,
        failed_step: StepRecord,
        failure_type: str,
    ) -> StepRecord:
        """查找替代工具执行

        策略：从 available_tools 中查找名称相似的工具
        （skill_service.find_fallback_skill 未实现，用启发式匹配）
        """
        failed_name = failed_step.tool_name or ""

        # 从 available_tools 查找同类型替代工具
        fallback_tool = _find_fallback_tool(state.available_tools, failed_name)

        if fallback_tool is None:
            # 无替代工具，返回原失败步骤
            state.add_event("tool_fallback", {
                "tool": failed_name, "fallback": None, "result": "no_alternative",
            })
            failed_step.tool_output = failed_step.tool_output or {}
            failed_step.tool_output["fallback_attempted"] = False
            return failed_step

        fallback_name = fallback_tool.get("function", {}).get("name", "")
        log.info("Falling back from {} to {}", failed_name, fallback_name)
        state.add_event("tool_fallback", {
            "tool": failed_name, "fallback": fallback_name,
            "failure_type": failure_type,
        })

        # 用替代工具执行（保留原参数，可能不完全匹配但让 LLM 后续纠正）
        from app.domain.agent.executor import execute_tool_call
        started = datetime.now()
        try:
            # 构造伪 tool_call（保留原 arguments）
            fallback_tc = _build_fallback_tc(tc, fallback_name)
            result = await execute_tool_call(state, fallback_tc)
            if result.status != "failed":
                # 标记为 fallback 成功
                result.step_name = f"fallback_{failed_name}_to_{fallback_name}"
                _prune_failed_history(state, failed_name)
            return result
        except Exception as e:
            log.warning("Fallback {} failed: {}", fallback_name, e)
            failed_step.error = f"{failed_step.error or ''}; fallback {fallback_name} also failed: {e}"
            return failed_step


def _find_fallback_tool(available_tools: list[dict], failed_name: str) -> Optional[dict]:
    """从可用工具列表查找替代工具（启发式：名称前缀匹配）"""
    if not failed_name or not available_tools:
        return None
    # 提取前缀（如 "search_kb" → "search"）
    prefix = failed_name.split("_")[0] if "_" in failed_name else failed_name[:4]
    for tool in available_tools:
        name = tool.get("function", {}).get("name", "")
        # 跳过自身、内置工具、不匹配的工具
        if name == failed_name:
            continue
        if name in ("finish", "delegate_to_subagent"):
            continue
        if prefix and prefix in name:
            return tool
    return None


def _build_fallback_tc(original_tc: Any, fallback_name: str) -> Any:
    """构造 fallback tool_call（保留原参数）"""
    from types import SimpleNamespace

    original_fn = original_tc.function
    return SimpleNamespace(
        id=original_tc.id,
        function=SimpleNamespace(
            name=fallback_name,
            arguments=original_fn.arguments,
        ),
    )


def _prune_failed_history(state: AgentState, tool_name: str) -> None:
    """清理指定工具的失败 tool_result 消息（防上下文污染）

    成功重试后，移除之前的失败 tool 消息，避免 LLM 误以为工具持续失败
    """
    if not state.messages:
        return
    # 仅保留非失败 tool_result（按 tool name 匹配）
    pruned = []
    for msg in state.messages:
        if (msg.get("role") == "tool"
                and msg.get("name") == tool_name
                and _is_failure_output(msg.get("content", ""))):
            continue  # 跳过失败历史
        pruned.append(msg)
    if len(pruned) < len(state.messages):
        state.messages = pruned


def _is_failure_output(content: str) -> bool:
    """判断 tool_result 内容是否为失败输出"""
    if not content:
        return False
    return any(kw in content for kw in ('"error"', '"failed"', "Error:", "failed:"))


# 单例
_healer: Optional[Healer] = None


def get_healer() -> Healer:
    """获取 Healer 单例"""
    global _healer
    if _healer is None:
        _healer = Healer()
    return _healer
