"""工作流节点执行器 - 各节点类型的具体执行逻辑

从 engine.py 抽离，保持 engine.py 聚焦 DAG 推进骨架（execute_node + WorkflowDefinition）。

新增节点类型（Phase B5）：
- parallel: asyncio.gather 并行多分支，汇聚结果到 context['outputs']['parallel']
- agent_call: 调用 AgentService.start_task_and_wait 启动子 Agent 任务，同步等待
- sub_workflow: 递归调用 WorkflowService.execute_workflow 执行子工作流
- condition 运算符扩展: == / != / > / < / >= / <= / contains / in

架构说明：
  本模块位于 Domain 层，exec_skill_call / exec_send_message / exec_agent_call /
  exec_sub_workflow 通过函数内延迟导入 app.service.* 调用 Service 层。
  这是一种工程妥协（同 domain/agent/nodes.py）：避免循环依赖的同时让节点能执行 IO。
"""
import asyncio
import operator
from typing import Any

from app.utils.logger import get_logger

log = get_logger("workflow_node_executors")

# 条件运算符映射
_OPS = {
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    "<": operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
    "contains": lambda a, b: b in a if a else False,
    "in": lambda a, b: a in b if b else False,
}


async def exec_skill_call(config: dict, context: dict, user_id: str) -> dict:
    """执行 Skill 调用"""
    from app.service.skill_service import skill_service

    skill_id = config.get("skill_id")
    args = config.get("args", {})
    args = _resolve_vars(args, context)
    if not skill_id:
        return {"error": "skill_id 未配置"}
    result = await skill_service.execute(skill_id, args, user_id=user_id)
    context.setdefault("outputs", {})[skill_id] = result
    return result


async def exec_llm_call(config: dict, context: dict) -> dict:
    """执行 LLM 调用（无工具，纯对话）"""
    from app.infra.llm.provider import get_llm

    prompt_template = config.get("prompt", "")
    prompt = _resolve_vars_str(prompt_template, context)
    system = config.get("system", "你是企业办公助手。")
    llm = get_llm()
    result = await llm.chat_completion([
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ])
    content = result.get("content", "")
    context.setdefault("outputs", {})["llm"] = content
    return {"content": content, "usage": result.get("usage")}


async def exec_send_message(config: dict, context: dict, chat_id: str) -> dict:
    """通过 ChannelService 发送消息"""
    from app.domain.channel.models import Channel
    from app.service.channel_service import channel_service

    channel = config.get("channel", "api")
    text = _resolve_vars_str(config.get("text", ""), context)
    try:
        channel_enum = Channel(channel)
    except ValueError:
        return {"error": f"未知渠道: {channel}"}
    result = await channel_service.send_text(channel_enum, chat_id, text)
    return {"sent": result.success, "message_id": result.message_id, "error": result.error}


async def exec_hitl(config: dict, context: dict) -> dict:
    """HITL 节点：暂停等待人工确认

    config.prompt 可定制确认提示文案。
    """
    return {
        "paused": True,
        "require_confirm": True,
        "prompt": config.get("prompt", "请确认是否继续执行"),
    }


async def exec_parallel(node: dict, context: dict, chat_id: str, user_id: str) -> dict:
    """并行执行多分支，汇聚结果到 context['outputs']['parallel']

    config.branches: list[dict]，每个 branch 是一个完整的 node 定义（含 type/config）。
    用 asyncio.gather 并发执行，单个分支失败不影响其他分支（return_exceptions=True）。
    """
    config = node.get("config", {})
    branches = config.get("branches", [])
    if not branches:
        return {"parallel_results": []}

    # 延迟 import 避免循环依赖（engine.py import 本模块，本模块回调 execute_node）
    from app.domain.workflow.engine import execute_node

    tasks = [execute_node(b, context, chat_id, user_id) for b in branches]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    parallel_results: list[dict] = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            log.warning("parallel branch {} failed: {}", i, r)
            parallel_results.append({"error": str(r), "branch_index": i})
        else:
            output, _ = r  # (output, next_ids) — 并行分支忽略 next_ids
            parallel_results.append(output)
    context.setdefault("outputs", {})["parallel"] = parallel_results
    return {"parallel_results": parallel_results}


async def exec_agent_call(config: dict, context: dict, chat_id: str, user_id: str) -> dict:
    """调用 AgentService 启动子 Agent 任务，同步等待结果

    config:
        message: str - Agent 任务消息（支持 ${var} 变量替换）
        context: dict - 传递给 Agent 的上下文（可选）
        timeout: int - 等待超时秒数（默认 120）
    """
    from app.service.agent_service import agent_service

    message = _resolve_vars_str(config.get("message", ""), context)
    agent_context = config.get("context", {})
    timeout = config.get("timeout", 120)
    result = await agent_service.start_task_and_wait(
        message=message, channel="workflow", chat_id=chat_id,
        user_id=user_id, context=agent_context, timeout=timeout,
    )
    context.setdefault("outputs", {})["agent_call"] = result
    return {"agent_result": result}


async def exec_sub_workflow(config: dict, context: dict, chat_id: str, user_id: str) -> dict:
    """递归调用 WorkflowService 执行子工作流

    config:
        workflow_id: str - 子工作流 ID
        inputs: dict - 传递给子工作流的输入（支持 ${var} 变量替换）
    """
    from app.service.workflow_service import workflow_service

    sub_id = config.get("workflow_id")
    if not sub_id:
        return {"error": "workflow_id 未配置"}
    sub_inputs = _resolve_vars(config.get("inputs", {}), context)
    result = await workflow_service.execute_workflow(
        workflow_id=sub_id, inputs=sub_inputs, chat_id=chat_id, user_id=user_id,
    )
    context.setdefault("outputs", {})["sub_workflow"] = result
    return {"sub_workflow_result": result}


def eval_condition_advanced(node: dict, config: dict, context: dict) -> list[str]:
    """支持运算符的条件评估

    新格式（带 op，Phase B5）：
        {"expr": "amount", "op": ">", "value": 100,
         "cases_true": ["node_a"], "cases_false": ["node_b"]}

    旧格式（兼容，仅等值匹配）：
        {"expr": "status", "cases": {"approved": ["node_a"]}, "default": ["node_c"]}
    """
    expr = config.get("expr", "")
    value = context.get("variables", {}).get(expr)

    # 新格式：带 op 字段
    op_str = config.get("op")
    if op_str:
        op_fn = _OPS.get(op_str)
        if op_fn is None:
            log.warning("Unknown operator: {}, fallback to ==", op_str)
            op_fn = operator.eq
        compare_value = config.get("value")
        try:
            # 类型转换：若 value 是数值而 compare_value 是字符串，尝试转换
            if isinstance(value, (int, float)) and isinstance(compare_value, str):
                compare_value = float(compare_value) if "." in compare_value else int(compare_value)
            result = op_fn(value, compare_value)
        except Exception as e:
            log.warning("condition eval failed: {} {} {}: {}", value, op_str, compare_value, e)
            result = False
        return config.get("cases_true", []) if result else config.get("cases_false", [])

    # 旧格式：cases 字典等值匹配（兼容）
    cases = config.get("cases", {})
    value_str = str(value) if value is not None else ""
    for case_value, next_ids in cases.items():
        if case_value == value_str:
            return next_ids
    return config.get("default", [])


def _resolve_vars(data: Any, context: dict) -> Any:
    """递归解析 ${var} 占位符"""
    if isinstance(data, str):
        return _resolve_vars_str(data, context)
    if isinstance(data, dict):
        return {k: _resolve_vars(v, context) for k, v in data.items()}
    if isinstance(data, list):
        return [_resolve_vars(v, context) for v in data]
    return data


def _resolve_vars_str(text: str, context: dict) -> str:
    """替换 ${var} 形式的变量引用"""
    if not text or "${" not in text:
        return text
    variables = context.get("variables", {})
    for k, v in variables.items():
        text = text.replace(f"${{{k}}}", str(v))
    return text
