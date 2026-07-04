"""LLM Judge - L4 独立终止评估器

每 K 步调轻量 LLM 评估 Agent 执行质量，决定是否终止：
- continue：继续执行
- stop：任务已完成，进入 COMPLETED
- failed：执行质量过差（如反复失败、无进展），进入 FAILED

设计：
- K=4（默认），每 4 步评估一次，控制成本
- max_tokens=80, temperature=0，输出严格 JSON
- 异常时 fallback continue 不阻断主链路
- 与 L3（行为终止）互补：L3 是确定性规则，L4 是语义判断
"""
import json
from typing import Any, Optional

from app.domain.agent.state import AgentState
from app.utils.logger import get_logger

log = get_logger("agent_judge")

JUDGE_PROMPT = """你是 Agent 执行质量评估器。评估当前 Agent 是否应终止执行。

用户原始请求：{original_message}
已执行步数：{step_count}/{max_steps}
累计 Token：{total_tokens}

最近步骤摘要：
{steps_summary}

判断标准：
- "continue": Agent 正在取得进展，应继续执行
- "stop": 已收集到足够信息，可以回答用户问题（任务完成）
- "failed": 反复失败无进展，无法完成任务

输出 JSON（仅 JSON）：
{{"verdict": "continue|stop|failed", "reason": "原因（20字内）"}}"""


class LLMJudge:
    """LLM 终止评估器实现

    通过 DI 注入到 AgentService（set_judge），在 graph.py 每 K 步调用一次。
    """

    def __init__(self) -> None:
        self._llm: Any = None

    def _get_llm(self):
        """延迟获取 LLM Provider（避免循环导入）"""
        if self._llm is None:
            from app.infra.llm.provider import get_llm
            self._llm = get_llm()
        return self._llm

    def should_run(self, state: AgentState, k: int) -> bool:
        """判断当前步是否需要触发 Judge 评估

        每 K 步触发一次（step % k == 0），且仅在 EXECUTING 状态触发
        """
        return state.current_step > 0 and state.current_step % k == 0

    async def evaluate(self, state: AgentState) -> dict:
        """评估当前 Agent 状态

        Returns:
            {"verdict": "continue"|"stop"|"failed", "reason": "..."}
            异常时 fallback 返回 continue
        """
        try:
            steps_summary = self._summarize_steps(state)
            prompt = JUDGE_PROMPT.format(
                original_message=state.original_message[:200],
                step_count=state.current_step,
                max_steps=state.max_steps,
                total_tokens=state.total_tokens,
                steps_summary=steps_summary,
            )
            llm = self._get_llm()
            result = await llm.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"},
                max_tokens=80,
            )
            content = result.get("content", "").strip()
            parsed = json.loads(content)
            verdict = parsed.get("verdict", "continue")
            reason = parsed.get("reason", "")[:50]
            if verdict not in ("continue", "stop", "failed"):
                verdict = "continue"
                reason = f"unknown_verdict:{verdict}"
            log.info(
                "Judge verdict={} reason={} (step={}/{})",
                verdict, reason, state.current_step, state.max_steps,
            )
            state.add_event("judge_evaluated", {
                "verdict": verdict, "reason": reason, "step": state.current_step,
            })
            return {"verdict": verdict, "reason": reason}
        except Exception as e:
            log.warning("Judge evaluate failed, fallback to continue: {}", e)
            return {"verdict": "continue", "reason": f"judge_error:{e}"}

    @staticmethod
    def _summarize_steps(state: AgentState) -> str:
        """摘要最近 4 步执行情况（控制 prompt 长度）"""
        recent = state.steps[-4:] if len(state.steps) >= 4 else state.steps
        if not recent:
            return "（无步骤）"
        lines = []
        for s in recent:
            status = s.status or "unknown"
            tool = s.tool_name or "unknown"
            err = f" err={s.error[:30]}" if s.error else ""
            lines.append(f"- step{s.step_index}: {tool} [{status}]{err}")
        return "\n".join(lines)


# 单例
_judge: Optional[LLMJudge] = None


def get_judge() -> LLMJudge:
    """获取 LLMJudge 单例"""
    global _judge
    if _judge is None:
        _judge = LLMJudge()
    return _judge
