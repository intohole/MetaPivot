"""Agent 终止条件检测（L3 行为终止）

四层终止条件中的 L3：基于行为模式的终止判断
- L1: finish 工具（LLM 显式标记完成）— 在 executor.py 早分支处理
- L2: 资源预算（步数/token）— 在 prompts.build_system_prompt 注入
- L3: 行为终止（doom loop / stuck on failure）— 本文件
- L4: 独立 Judge LLM — 在 judge.py 处理

设计：
- is_doom_loop：最近 4 步 args_hash 全相同（重复调用同工具同参数 → 死循环）
- is_stuck_on_failure：连续 3 次同工具失败（兼容旧 _is_stuck 语义）
- should_terminate：综合 L3 判断，返回 (是否终止, 原因)
"""
import hashlib
import json

from app.domain.agent.state import AgentState, StepRecord


def _args_hash(step: StepRecord) -> str:
    """计算步骤的参数哈希（tool_name + tool_input 的 md5）

    用于检测重复调用：同工具同参数的连续调用是死循环的强信号
    """
    if not step.tool_name:
        return ""
    payload = json.dumps(
        {"t": step.tool_name, "i": step.tool_input},
        sort_keys=True, default=str,
    )
    return hashlib.md5(payload.encode()).hexdigest()


def is_doom_loop(state: AgentState) -> bool:
    """检测死循环：最近 4 步 args_hash 全相同

    场景：LLM 反复调用同一工具同一参数，无法取得进展
    阈值 4 步避免误判（2-3 步可能是正常重试）
    """
    recent = state.steps[-4:] if len(state.steps) >= 4 else []
    hashes = [h for h in (_args_hash(s) for s in recent) if h]
    # 至少 2 个 hash 且全部相同 → 死循环
    return len(hashes) >= 2 and len(set(hashes)) == 1


def is_stuck_on_failure(state: AgentState) -> bool:
    """检测卡住：连续 3 次同工具失败

    兼容旧 nodes.py:_is_stuck 语义，用于 should_terminate 综合判断
    """
    if len(state.steps) < 3:
        return False
    recent = state.steps[-3:]
    tool_names = [s.tool_name for s in recent]
    statuses = [s.status for s in recent]
    # 同工具 + 全部失败
    return len(set(tool_names)) == 1 and all(s == "failed" for s in statuses)


def should_terminate(state: AgentState) -> tuple[bool, str]:
    """综合 L3 终止判断

    Returns:
        (是否终止, 原因) — True 时 Agent 应进入 FAILED
    """
    if is_doom_loop(state):
        return True, "doom_loop_detected"
    if is_stuck_on_failure(state):
        return True, "stuck_on_failure"
    return False, ""
