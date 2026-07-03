"""WorkflowEngine - DAG 工作流执行引擎

支持的节点类型：
- start: 入口节点
- end: 结束节点
- skill_call: 调用 Skill
- llm_call: 调用 LLM（无工具）
- condition: 条件分支（config.expr 表达式）
- send_message: 通过 ChannelService 发送消息
- hitl: 暂停等待人工确认

执行模型：
- 节点按 next 列表推进
- condition 节点根据 config.expr 求值选择分支
- hitl 节点暂停执行，等待外部 confirm 后继续

架构说明：
  本模块位于 Domain 层，但 _exec_skill_call / _exec_send_message 通过
  函数内延迟导入 app.service.{skill,channel}_service 调用 Service 层。
  这是一种工程妥协（同 domain/agent/nodes.py）：避免循环依赖的同时
  让节点能执行 IO。严格 Domain 纯净化改造方向：定义 NodeRuntime Protocol，
  由 WorkflowService 注入实现。当前妥协可接受，列为后续优化项。
"""
from typing import Any, Optional

from app.utils.logger import get_logger
from app.utils.response import AppError, ErrorCode

log = get_logger("workflow_engine")

# 支持的节点类型
NODE_TYPES = {"start", "end", "skill_call", "llm_call", "condition", "send_message", "hitl"}


class WorkflowDefinition:
    """工作流定义（运行时形态）"""

    def __init__(self, definition: dict) -> None:
        nodes = definition.get("nodes", [])
        edges = definition.get("edges", [])
        if not nodes:
            raise AppError(ErrorCode.WORKFLOW_INVALID, "工作流节点为空", 400)

        self.nodes: dict[str, dict] = {n["id"]: n for n in nodes}
        if len(self.nodes) != len(nodes):
            raise AppError(ErrorCode.WORKFLOW_INVALID, "节点ID重复", 400)

        # 邻接表：node_id -> [next_node_id]
        self.adj: dict[str, list[str]] = {nid: [] for nid in self.nodes}
        for e in edges:
            src, dst = e.get("source"), e.get("target") or e.get("dst")
            if src not in self.nodes or dst not in self.nodes:
                raise AppError(ErrorCode.WORKFLOW_INVALID, f"非法边: {src} -> {dst}", 400)
            self.adj[src].append(dst)

        # 校验节点类型
        for nid, node in self.nodes.items():
            if node.get("type") not in NODE_TYPES:
                raise AppError(
                    ErrorCode.WORKFLOW_INVALID,
                    f"节点 {nid} 类型不支持: {node.get('type')}",
                    400,
                )

        self.start_node = self._find_start()
        self.variables: dict = definition.get("variables", [])

    def _find_start(self) -> str:
        for nid, node in self.nodes.items():
            if node.get("type") == "start":
                return nid
        # 兜底：取第一个节点
        return next(iter(self.nodes))


async def execute_node(
    node: dict,
    context: dict,
    chat_id: str,
    user_id: str,
) -> tuple[dict, list[str]]:
    """执行单个节点，返回 (输出, 下一步节点IDs)

    context 包含 inputs / variables / 历史节点输出
    """
    node_type = node.get("type")
    config = node.get("config", {})
    node_id = node.get("id")
    log.info("Executing node: {} ({})", node_id, node_type)

    if node_type == "start":
        return {}, _next_nodes(node, context)

    if node_type == "end":
        return {"finished": True}, []

    if node_type == "skill_call":
        return await _exec_skill_call(config, context, user_id), _next_nodes(node, context)

    if node_type == "llm_call":
        return await _exec_llm_call(config, context), _next_nodes(node, context)

    if node_type == "send_message":
        return await _exec_send_message(config, context, chat_id), _next_nodes(node, context)

    if node_type == "condition":
        next_ids = _eval_condition(node, config, context)
        return {"branched_to": next_ids}, next_ids

    if node_type == "hitl":
        # 暂停，由 WorkflowService 处理 confirm 后再推进
        return {"paused": True, "require_confirm": True}, []

    log.warning("Unknown node type, skip: {}", node_type)
    return {}, _next_nodes(node, context)


def _next_nodes(node: dict, context: dict) -> list[str]:
    """默认下一节点（基于邻接表）"""
    return context.get("__adj", {}).get(node["id"], [])


def _eval_condition(node: dict, config: dict, context: dict) -> list[str]:
    """条件分支：根据 config.cases 求值选择分支

    config 形如：
        {"expr": "status", "cases": {"approved": ["node_a"], "rejected": ["node_b"]}, "default": ["node_c"]}
    简化实现：expr 作为变量名从 context 取值，匹配 cases。
    """
    expr = config.get("expr", "")
    value = str(context.get("variables", {}).get(expr, ""))
    cases = config.get("cases", {})
    for case_value, next_ids in cases.items():
        if case_value == value:
            return next_ids
    return config.get("default", [])


async def _exec_skill_call(config: dict, context: dict, user_id: str) -> dict:
    """执行 Skill 调用"""
    from app.service.skill_service import skill_service

    skill_id = config.get("skill_id")
    args = config.get("args", {})
    # 支持 ${var} 变量替换
    args = _resolve_vars(args, context)
    if not skill_id:
        return {"error": "skill_id 未配置"}
    result = await skill_service.execute(skill_id, args, user_id=user_id)
    # 输出存入上下文
    context.setdefault("outputs", {})[skill_id] = result
    return result


async def _exec_llm_call(config: dict, context: dict) -> dict:
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


async def _exec_send_message(config: dict, context: dict, chat_id: str) -> dict:
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
