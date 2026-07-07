"""Cycle detection - 防 agent↔workflow 循环调用死锁

执行栈追踪：context["__exec_stack"] 存储 [agent_task_id, workflow_id, ...]
- agent→workflow：trigger_workflow 工具检查 workflow_id 是否在栈中
- workflow→agent：exec_agent_call 把栈传给子 agent 的 context
- max_depth 兜底：栈深度超过 MAX_DEPTH 拒绝执行

简化方案：用列表追踪执行栈，O(n) 查重，n 通常 ≤ 3。
不引入图算法，避免过度工程化。
"""
from typing import List

# 最大嵌套深度（agent→workflow→agent→workflow 终止）
MAX_DEPTH = 3


def check_cycle(exec_stack: List[str], target_id: str) -> bool:
    """检查目标 ID 是否已在执行栈中（循环检测）

    Args:
        exec_stack: 执行栈列表 [id1, id2, ...]
        target_id: 即将执行的目标 ID（workflow_id 或 agent_task_id）

    Returns:
        True 表示检测到循环，应拒绝执行
    """
    return target_id in exec_stack


def check_depth(exec_stack: List[str]) -> bool:
    """检查执行栈深度是否超限

    Returns:
        True 表示深度超限，应拒绝执行
    """
    return len(exec_stack) >= MAX_DEPTH


def push_id(exec_stack: List[str], id_: str) -> List[str]:
    """Push ID 到执行栈（返回新栈，避免原地修改共享状态）

    使用不可变模式：返回新列表，调用方需显式赋值。
    """
    return exec_stack + [id_]


def describe_stack(exec_stack: List[str]) -> str:
    """执行栈可读描述（用于错误消息和日志）

    Returns:
        "agent:abc123 → workflow:def456" 格式的字符串
    """
    if not exec_stack:
        return "(空)"
    return " → ".join(exec_stack)


def should_block(exec_stack: List[str], target_id: str) -> tuple:
    """综合检查：是否应阻止执行 + 阻止原因

    Returns:
        (should_block: bool, reason: str)
        should_block=True 时 reason 含阻止原因，调用方应拒绝执行
    """
    if check_depth(exec_stack):
        return True, f"执行栈深度超限（max {MAX_DEPTH}）：{describe_stack(exec_stack)}"
    if check_cycle(exec_stack, target_id):
        return True, f"检测到循环调用（{target_id} 已在执行栈中）：{describe_stack(exec_stack)}"
    return False, ""
