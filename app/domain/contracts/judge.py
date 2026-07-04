"""IJudge / IHealer - Agent 终止评估与自愈抽象接口

用于 Agent 核心深化（Phase 1）：
- IJudge：L4 独立 Judge LLM，每 K 步评估是否应终止（避免无限循环/低质量执行）
- IHealer：OODA 自愈循环，工具调用失败时分类 + 重试 + fallback

接口约束：
- evaluate() 返回 {verdict: continue|stop|failed, reason}，异常时 fallback continue
- execute_with_healing() 返回 StepRecord，内含重试与 fallback 决策
"""
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IJudge(Protocol):
    """L4 终止评估器接口（独立 LLM Judge）"""

    async def evaluate(self, state: Any) -> dict:
        """评估当前 Agent 状态是否应终止

        Args:
            state: AgentState 实例

        Returns:
            {"verdict": "continue"|"stop"|"failed", "reason": "..."}
            - continue: 继续执行
            - stop: 任务已完成，进入 COMPLETED
            - failed: 执行质量过差，进入 FAILED
            异常时应 fallback 返回 {"verdict": "continue", "reason": "judge_error"}
        """
        ...

    def should_run(self, state: Any, k: int) -> bool:
        """判断当前步是否需要触发 Judge 评估（每 K 步一次）"""
        ...


@runtime_checkable
class IHealer(Protocol):
    """OODA 自愈执行器接口"""

    async def execute_with_healing(
        self,
        state: Any,
        tc: Any,
        executor_fn: Any,
    ) -> Any:
        """带自愈的工具调用执行

        Args:
            state: AgentState 实例
            tc: tool_call 对象（OpenAI 格式）
            executor_fn: 原始 execute_tool_call 函数（单次执行，无重试）

        Returns:
            StepRecord — 成功或 fallback 后的步骤记录
        """
        ...
