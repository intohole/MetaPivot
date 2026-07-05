"""LLM 反思器 - 评估工具调用结果，决定下一步动作

设计（targeted reflection，避免每步都调用 LLM 增加成本）：
- 反思仅在以下场景触发：
  1. 最近的工具调用失败（决定 retry / 换工具 / give_up）
  2. 已用步数 ≥ max_steps - 2（接近上限，决定是否收尾）
  3. 检测到循环（同一工具连续调用 ≥ 2 次）
- 其他情况走"快速路径"：让 executor 的 LLM 通过 tool_choice="auto" 自主决策
  （OpenAI 工具调用模式本身就是隐式反思 — LLM 看到工具结果决定下一步）

输出：REFLECT_DECISION 枚举 + reason + hint（Phase B3）
- COMPLETE: 工具结果足够，进入 replier
- CONTINUE: 还需更多工具调用，回到 executor
- GIVE_UP: 无法完成，FAILED
- REPLAN: 计划偏差大，需要重新规划（Phase B3，由 graph.py 触发 planner.update_plan）

Phase B3: hint 字段为结构化修正建议，executor 通过 build_system_prompt 注入，
对齐 Claude Code 的反思-修正闭环。
"""
import json
from enum import Enum
from typing import Optional

from app.domain.agent.prompts import REFLECT_PROMPT
from app.domain.agent.state import AgentState, AgentStatus
from app.utils.logger import get_logger

log = get_logger("agent_reflector")


class ReflectDecision(str, Enum):
    """反思决策枚举"""
    COMPLETE = "complete"
    CONTINUE = "continue"
    GIVE_UP = "give_up"
    REPLAN = "replan"  # Phase B3: 计划偏差大，触发 re-planning


async def reflect(state: AgentState, llm_provider: object) -> tuple[ReflectDecision, str, dict]:
    """LLM 反思：评估当前状态决定下一步

    Args:
        state: Agent 状态
        llm_provider: LLM Provider 实例

    Returns:
        (decision, reason, hint) — hint 为结构化修正建议 dict，可空
        hint 形如 {"next_tool_hint": "search_kb", "why": "原工具连续失败"}
    """
    last_step = state.steps[-1] if state.steps else None
    last_tool_result = _summarize_last_result(last_step)

    prompt = REFLECT_PROMPT.format(
        original_message=state.original_message[:200],
        step_count=state.current_step,
        max_steps=state.max_steps,
        last_tool_result=last_tool_result[:500],
    )

    try:
        result = await llm_provider.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            response_format={"type": "json_object"},
            max_tokens=150,  # Phase B3: 增加 token 预算容纳 hint
        )
        content = result.get("content", "").strip()
        parsed = json.loads(content)
        decision_str = parsed.get("decision", "continue")
        reason = parsed.get("reason", "")[:50]
        hint = parsed.get("hint") or {}  # Phase B3: 解析 hint
        if not isinstance(hint, dict):
            hint = {}
        try:
            decision = ReflectDecision(decision_str)
        except ValueError:
            decision = ReflectDecision.CONTINUE
            reason = f"unknown_decision:{decision_str}"
        log.info(
            "Reflect: decision={} reason={} hint={} (step={}/{})",
            decision.value, reason, hint, state.current_step, state.max_steps,
        )
        return decision, reason, hint
    except Exception as e:
        log.warning("LLM reflect failed, fallback to CONTINUE: {}", e)
        return ReflectDecision.CONTINUE, f"reflect_error:{e}", {}


def should_reflect(state: AgentState) -> bool:
    """判断当前是否需要触发 LLM 反思（避免每步都调用，控制成本）

    触发条件（任一满足）：
    1. 最近的工具调用 failed（决定 retry / 换工具 / give_up）
    2. 已用步数 ≥ max_steps - 2（接近上限，决定是否收尾）
    3. 同一工具连续调用 ≥ 2 次（疑似循环）
    """
    if not state.steps:
        return False

    # 条件 1：最近工具失败
    last_step = state.steps[-1]
    if last_step.status == "failed":
        return True

    # 条件 2：接近步数上限
    if state.current_step >= state.max_steps - 2:
        return True

    # 条件 3：同一工具连续调用 ≥ 2 次
    recent = state.steps[-2:] if len(state.steps) >= 2 else []
    if len(recent) == 2:
        if (recent[0].tool_name == recent[1].tool_name
                and recent[0].tool_name is not None):
            return True

    return False


def apply_reflect_decision(
    state: AgentState,
    decision: ReflectDecision,
    reason: str,
    hint: Optional[dict] = None,
) -> None:
    """将反思决策应用到 state（更新 status + 存储 hint）

    Phase B3: REPLAN 决策标记 need_replan，由 graph.py 检测后触发 planner.update_plan
    Phase B3: hint 存到 state.context["reflection_hint"]，executor 通过 build_system_prompt 注入
    """
    if decision == ReflectDecision.COMPLETE:
        state.status = AgentStatus.COMPLETED
    elif decision == ReflectDecision.GIVE_UP:
        state.status = AgentStatus.FAILED
        state.error = {
            "code": "AGENT_REFLECT_GIVE_UP",
            "message": f"反思放弃: {reason}",
        }
    elif decision == ReflectDecision.REPLAN:
        # Phase B3: 标记需要 re-planning，graph.py 检测到后调 planner.update_plan
        state.status = AgentStatus.EXECUTING  # 保持执行状态，graph 层处理 replan
        state.context["need_replan"] = True
        state.context["replan_reason"] = reason
    else:  # CONTINUE
        state.status = AgentStatus.EXECUTING

    # Phase B3: 存储 hint 到 state.context，executor 通过 build_system_prompt 注入 system prompt
    if hint:
        state.context["reflection_hint"] = hint

    state.add_event("reflected", {
        "decision": decision.value, "reason": reason,
        "hint": hint or None,
        "step": state.current_step,
    })


def _summarize_last_result(step) -> str:
    """摘要最后一步的工具结果（避免 prompt 过长）"""
    if step is None:
        return "（无步骤）"
    if step.status == "failed":
        return f"工具 {step.tool_name} 失败: {step.error or ''}"
    if step.tool_output:
        try:
            text = json.dumps(step.tool_output, ensure_ascii=False, default=str)
            return text[:300]
        except Exception:
            return str(step.tool_output)[:300]
    return f"工具 {step.tool_name} 完成（无输出）"
