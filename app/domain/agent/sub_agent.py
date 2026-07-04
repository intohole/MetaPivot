"""子代理 Worker - 轻量级子任务委托执行

设计（Claude Code orchestrator-worker 模式）：
- 子代理拥有独立 AgentState（独立上下文窗口，避免污染父任务）
- 递归调用 run_agent（复用主链路）
- MAX_DEPTH=2 防递归死锁
- 子代理移除 finish/delegate 工具（防递归调用自身）
- 执行完成后仅返回浓缩结论（answer/tokens/steps）

适用场景：
- 信息检索汇总（子代理检索多个来源后返回汇总）
- 多步骤分析（子代理完成独立分析后返回结论）
- 并行子任务（多个子代理并行处理独立子任务）
"""
from datetime import datetime
from uuid import uuid4

from app.domain.agent.graph import run_agent
from app.domain.agent.state import AgentState
from app.utils.logger import get_logger

log = get_logger("agent_sub")

# 最大嵌套深度（防递归死锁）
MAX_DEPTH = 2


async def spawn_sub_agent(
    parent: AgentState,
    message: str,
    max_steps: int = 5,
) -> dict:
    """派生子代理执行子任务

    Args:
        parent: 父 AgentState
        message: 子任务描述（应为完整、可独立执行的指令）
        max_steps: 子代理最大步数（默认 5，上限 10）

    Returns:
        {"answer": str, "tokens": int, "steps": int, "sub_task_id": str}
        失败时 answer 含错误说明
    """
    # 递归深度保护
    if parent.depth >= MAX_DEPTH:
        log.warning(
            "Sub-agent depth limit reached: parent.depth={} >= MAX_DEPTH={}",
            parent.depth, MAX_DEPTH,
        )
        return {
            "answer": f"子代理嵌套深度超限（最大 {MAX_DEPTH} 层）",
            "tokens": 0, "steps": 0, "sub_task_id": "",
        }

    # 限制子代理步数（防止子代理失控）
    max_steps = max(1, min(max_steps, 10))

    # 构造子代理状态（独立上下文）
    sub_task_id = f"sub-{parent.task_id}-{uuid4().hex[:6]}"
    sub_state = AgentState(
        task_id=sub_task_id,
        user_id=parent.user_id,
        channel=parent.channel,
        chat_id=parent.chat_id,
        original_message=message,
        context={"parent_task_id": parent.task_id},
        max_steps=max_steps,
        # 独立消息历史（不继承父任务，仅用子任务描述）
        messages=[],
        started_at=datetime.now(),
        # 子代理标识
        parent_task_id=parent.task_id,
        depth=parent.depth + 1,
        request_id=parent.request_id,
        trace_id=parent.trace_id,
    )

    # 过滤工具：移除 finish/delegate（防递归调用自身）
    sub_state.available_tools = [
        t for t in parent.available_tools
        if t.get("function", {}).get("name") not in ("finish", "delegate_to_subagent")
    ]

    log.info(
        "Spawning sub-agent: sub_task_id={} parent={} depth={} tools={} max_steps={}",
        sub_task_id, parent.task_id, sub_state.depth,
        len(sub_state.available_tools), max_steps,
    )

    # 递归调用 run_agent（复用主链路）
    try:
        async for event in run_agent(sub_state):
            # 将子代理事件透传给父任务（带 sub_task_id 标记）
            parent.add_event("sub_agent_event", {
                "sub_task_id": sub_task_id,
                "event": event,
            })
    except Exception as e:
        log.exception("Sub-agent {} crashed: {}", sub_task_id, e)
        return {
            "answer": f"子代理执行失败: {e}",
            "tokens": sub_state.total_tokens,
            "steps": sub_state.current_step,
            "sub_task_id": sub_task_id,
        }

    log.info(
        "Sub-agent completed: sub_task_id={} status={} steps={} tokens={}",
        sub_task_id, sub_state.status.value,
        sub_state.current_step, sub_state.total_tokens,
    )

    return {
        "answer": sub_state.final_answer or "(子代理无输出)",
        "tokens": sub_state.total_tokens,
        "steps": sub_state.current_step,
        "sub_task_id": sub_task_id,
    }
