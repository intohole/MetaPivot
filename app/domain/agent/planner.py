"""Plan-Execute 规划器 - LLM 生成多步执行计划

设计：
- planner_node 调用 LLM 生成结构化执行计划（list of {step, tool, purpose}）
- 计划存入 state.plan，作为 executor LLM 的高层上下文
- executor 仍用 tool_choice="auto" 决定具体调用，但 plan 提供方向指引
- 计划失败（LLM 不可用/JSON 解析失败）时降级为空 plan，不影响执行

权衡：
- 优点：复杂多步任务有明确路线图，减少 LLM 在 executor 中"瞎试"
- 缺点：多一次 LLM 调用（增加延迟和成本）
- 折中：仅 AGENT 模式触发；PIPELINE/WORKFLOW 跳过

调用时机：
- planner_node 中，mode=AGENT 时调用
- 失败不阻塞（plan 为空时 executor 仍可正常 tool_choice="auto"）
"""
import json
from typing import Optional

from app.domain.agent.prompts import PLAN_PROMPT
from app.domain.agent.state import AgentState
from app.utils.logger import get_logger

log = get_logger("agent_planner")


async def generate_plan(state: AgentState, llm_provider: object) -> list[dict]:
    """调用 LLM 生成执行计划

    Args:
        state: Agent 状态（含 original_message / available_tools）
        llm_provider: LLM Provider 实例

    Returns:
        计划列表，每项形如 {"step": 1, "tool": "...", "purpose": "..."}
        失败时返回空列表（不阻塞执行）
    """
    if not state.available_tools:
        return []

    tools_desc = _format_tools_for_plan(state.available_tools)
    prompt = PLAN_PROMPT.format(message=state.original_message, tools_desc=tools_desc)

    try:
        result = await llm_provider.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            response_format={"type": "json_object"},
            max_tokens=300,
        )
        content = result.get("content", "").strip()
        parsed = json.loads(content)
        plan = parsed.get("plan", [])
        if not isinstance(plan, list):
            return []
        # 校验每项结构
        validated: list[dict] = []
        for item in plan[:5]:  # 最多 5 步
            if isinstance(item, dict) and "tool" in item:
                validated.append({
                    "step": item.get("step", len(validated) + 1),
                    "tool": str(item.get("tool", ""))[:64],
                    "purpose": str(item.get("purpose", ""))[:50],
                })
        log.info("Plan generated: {} steps", len(validated))
        return validated
    except Exception as e:
        log.warning("generate_plan failed: {}", e)
        return []


def format_plan_as_context(plan: list[dict]) -> str:
    """将计划格式化为可注入 system prompt 的上下文文本"""
    if not plan:
        return ""
    lines = ["\n\n【执行计划参考（LLM 可自主调整）】"]
    for item in plan:
        lines.append(f"{item.get('step', '?')}. {item.get('tool', '?')} - {item.get('purpose', '')}")
    return "\n".join(lines)


def _format_tools_for_plan(tools: list[dict]) -> str:
    """格式化工具列表供 PLAN_PROMPT 使用"""
    lines = []
    for t in tools[:15]:
        func = t.get("function", {})
        name = func.get("name", "unknown")
        desc = func.get("description", "")[:60]
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines) if lines else "（无工具）"


# ============ Phase B1: Re-planning ============

_MAX_REVISIONS = 2  # 防 re-planning 震荡


async def update_plan(state: AgentState, llm_provider: object, reason: str = "") -> list[dict]:
    """Phase B1: 动态更新计划（Re-planning）

    触发条件：reflector 输出 REPLAN，或 graph 检测到 plan 偏差大

    Args:
        state: Agent 状态（含已完成 steps + 剩余 plan + reflection_hint）
        llm_provider: LLM Provider 实例
        reason: re-plan 原因（来自 reflector）

    Returns:
        新的计划列表；超过 max_revisions 返回空列表（强制走 executor auto）
    """
    if state.plan_revision_count >= _MAX_REVISIONS:
        log.warning("Re-plan limit reached ({}), fallback to executor auto", _MAX_REVISIONS)
        return []

    if not state.available_tools:
        return []

    from app.domain.agent.prompts import REPLAN_PROMPT

    completed_summary = _summarize_completed_steps(state.steps)
    remaining_plan = state.plan[state.current_step:] if state.current_step < len(state.plan) else []
    tools_desc = _format_tools_for_plan(state.available_tools)
    prompt = REPLAN_PROMPT.format(
        message=state.original_message,
        tools_desc=tools_desc,
        completed=completed_summary,
        remaining_plan=json.dumps(remaining_plan, ensure_ascii=False),
        reason=reason,
    )

    try:
        result = await llm_provider.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            response_format={"type": "json_object"},
            max_tokens=300,
        )
        content = result.get("content", "").strip()
        parsed = json.loads(content)
        plan = parsed.get("plan", [])
        if not isinstance(plan, list):
            return []
        validated: list[dict] = []
        for item in plan[:5]:
            if isinstance(item, dict) and "tool" in item:
                validated.append({
                    "step": item.get("step", len(validated) + 1),
                    "tool": str(item.get("tool", ""))[:64],
                    "purpose": str(item.get("purpose", ""))[:50],
                })
        state.plan_revision_count += 1
        log.info("Plan revised: {} steps (revision #{})", len(validated), state.plan_revision_count)
        state.add_event("plan_revised", {
            "reason": reason, "new_steps": len(validated),
            "revision": state.plan_revision_count,
        })
        return validated
    except Exception as e:
        log.warning("update_plan failed: {}", e)
        return []


def _summarize_completed_steps(steps: list) -> str:
    """摘要已完成的步骤（供 re-plan prompt 用）"""
    if not steps:
        return "（无已完成步骤）"
    lines = []
    for s in steps[-5:]:  # 最近 5 步
        status = s.status or "unknown"
        tool = s.tool_name or "?"
        lines.append(f"- step {s.step_index}: {tool} ({status})")
    return "\n".join(lines)
