"""LLMVerifier - Phase 4.2 结果验证器实现

在 Agent 执行完成后、生成最终回复前，对结果质量做独立验证。

设计要点：
- 使用 llm_json_call（复用 Phase 4.1 基础设施，内置熔断 + JSON 容错）
- LLM 不可用时 fallback 为 VERIFIED（不阻断主链路，符合"渐进增强"语义）
- 验证维度：相关性 / 完整性 / 准确性（防幻觉）
- 与 Judge（执行中终止控制）/ Reflector（执行中动作选择）互补
- 仅对 AGENT 模式触发（PIPELINE 模式无工具调用，验证意义有限）
"""
import json
from typing import Any, Optional

from app.domain.contracts.verifier import VerifyDecision, VerifyResult
from app.utils.llm_structured import llm_json_call
from app.utils.logger import get_logger

log = get_logger("agent_verifier")

VERIFY_SYSTEM_PROMPT = """你是 Agent 结果验证器。评估 Agent 的最终回复是否准确回答了用户问题。

验证维度：
1. 相关性：回复是否针对用户原始问题，而非答非所问
2. 完整性：是否遗漏关键信息（如用户问了多个子问题，是否都回答了）
3. 准确性：回复内容是否与工具返回的事实一致（检测幻觉/编造）

判断标准：
- "verified": 回复准确、完整、相关，可直接交付用户
- "needs_revision": 回复基本可用但有瑕疵（如遗漏次要信息、措辞不准），需附加提示
- "failed": 回复严重错误（幻觉、答非所问、与工具结果矛盾），不应交付

输出 JSON（仅 JSON）：
{
  "decision": "verified|needs_revision|failed",
  "reason": "决策原因（30字内）",
  "confidence": 0.0-1.0,
  "caveats": ["给用户的提示条目（needs_revision 时填写，可空）"]
}"""

VERIFY_USER_TEMPLATE = """用户原始问题：
{original_message}

Agent 最终回复：
{final_answer}

工具调用结果摘要（用于核对事实）：
{tool_results}

已执行步数：{step_count}/{max_steps}"""


class LLMVerifier:
    """LLM 驱动的结果验证器

    通过 DI（set_verifier）注入到 AgentService，graph.py 在执行循环结束后
    调用 verify()。LLM 不可用时降级为 VERIFIED（渐进增强语义：宁可不验证，不可阻断）。
    """

    def __init__(self) -> None:
        self._llm: Any = None

    def _get_llm(self):
        """延迟获取 LLM Provider（避免循环导入）"""
        if self._llm is None:
            from app.infra.llm.provider import get_llm
            self._llm = get_llm()
        return self._llm

    async def verify(self, state: object) -> VerifyResult:
        """验证 Agent 执行结果

        Args:
            state: AgentState 实例（运行时断言类型）

        Returns:
            VerifyResult — VERIFIED/NEEDS_REVISION/FAILED + caveats
        """
        # 渐进增强：无最终答案时直接通过（PIPELINE 模式可能在 stream reply 前验证）
        final_answer = getattr(state, "final_answer", "") or ""
        if not final_answer.strip():
            return VerifyResult(
                decision=VerifyDecision.VERIFIED,
                reason="empty_answer_skip",
                confidence=0.5,
            )

        original_message = getattr(state, "original_message", "")[:300]
        steps = getattr(state, "steps", []) or []
        step_count = getattr(state, "current_step", 0)
        max_steps = getattr(state, "max_steps", 10)
        tool_results = self._summarize_tool_results(steps)

        user_input = VERIFY_USER_TEMPLATE.format(
            original_message=original_message,
            final_answer=final_answer[:800],
            tool_results=tool_results,
            step_count=step_count,
            max_steps=max_steps,
        )

        # fallback: LLM 不可用时降级为 VERIFIED（不阻断主链路）
        fallback = {
            "decision": "verified",
            "reason": "llm_unavailable_skip",
            "confidence": 0.0,
            "caveats": [],
        }

        try:
            parsed = await llm_json_call(
                system_prompt=VERIFY_SYSTEM_PROMPT,
                user_input=user_input,
                temperature=0.0,
                max_tokens=200,
                fallback=fallback,
            )
        except Exception as e:
            log.warning("Verifier LLM call failed, fallback to VERIFIED: {}", e)
            return VerifyResult(
                decision=VerifyDecision.VERIFIED,
                reason=f"verifier_error:{e}",
                confidence=0.0,
            )

        decision_str = parsed.get("decision", "verified")
        reason = parsed.get("reason", "")[:80]
        confidence = float(parsed.get("confidence", 0.5))
        caveats = parsed.get("caveats") or []
        if not isinstance(caveats, list):
            caveats = []

        try:
            decision = VerifyDecision(decision_str)
        except ValueError:
            decision = VerifyDecision.VERIFIED
            reason = f"unknown_decision:{decision_str}"

        log.info(
            "Verify: decision={} reason={} confidence={:.2f} caveats={} (step={}/{})",
            decision.value, reason, confidence, len(caveats),
            step_count, max_steps,
        )

        # 记录验证事件到 state（供 SSE 订阅者感知验证结果）
        add_event = getattr(state, "add_event", None)
        if add_event:
            add_event("verified", {
                "decision": decision.value,
                "reason": reason,
                "confidence": confidence,
                "caveats": caveats,
            })

        return VerifyResult(
            decision=decision,
            reason=reason,
            caveats=[str(c)[:200] for c in caveats],
            confidence=confidence,
        )

    @staticmethod
    def _summarize_tool_results(steps: list) -> str:
        """摘要工具调用结果（用于 LLM 核对事实，防幻觉）

        只取成功的工具调用输出，控制 prompt 长度。
        """
        if not steps:
            return "（无工具调用）"
        lines = []
        for s in steps[-6:]:  # 最近 6 步
            tool = getattr(s, "tool_name", None) or "unknown"
            status = getattr(s, "status", "unknown")
            output = getattr(s, "tool_output", None)
            if output:
                try:
                    text = json.dumps(output, ensure_ascii=False, default=str)
                    text = text[:200]
                except Exception:
                    text = str(output)[:200]
            else:
                text = "（无输出）"
            lines.append(f"- {tool} [{status}]: {text}")
        return "\n".join(lines)


# 单例
_verifier: Optional[LLMVerifier] = None


def get_verifier() -> LLMVerifier:
    """获取 LLMVerifier 单例"""
    global _verifier
    if _verifier is None:
        _verifier = LLMVerifier()
    return _verifier


async def post_verify(state) -> tuple[list[dict], bool]:
    """Phase 4.2: 执行后结果验证（Sprint 7.5 从 graph.py 抽离）

    在 Agent 回复生成后、final_result 事件前验证质量。
    LLM 不可用时 verifier 内部降级为 VERIFIED，不阻断主链路（渐进增强语义）。

    Returns:
        (events, should_return) — events 为待 yield 的 SSE 事件列表；
        should_return=True 时调用方应立即 return（如 FAILED 决策）。
    """
    events: list[dict] = []
    verify_result = await get_verifier().verify(state)
    # drain verify() 内部 add_event 累积的节点级事件
    events.extend(list(state.events))
    state.events.clear()

    if verify_result.decision == VerifyDecision.FAILED:
        from app.domain.agent.state import AgentStatus
        state.status = AgentStatus.FAILED
        state.error = {
            "code": "VERIFIER_FAILED",
            "message": f"结果验证失败: {verify_result.reason}",
        }
        events.append({"type": "final_result", "data": {
            "answer": state.final_answer or "",
            "result": state.result,
            "error": state.error,
        }})
        return events, True

    if verify_result.decision == VerifyDecision.NEEDS_REVISION and verify_result.caveats:
        caveat_text = "\n\n---\n⚠️ **验证提示**：\n" + "\n".join(
            f"- {c}" for c in verify_result.caveats
        )
        state.final_answer = (state.final_answer or "") + caveat_text
        state.result = {
            **(state.result or {}),
            "verification_caveats": verify_result.caveats,
        }
    return events, False
