"""WorkflowEngine - DAG 工作流执行引擎

支持的节点类型：
- start: 入口节点
- end: 结束节点
- skill_call: 调用 Skill
- llm_call: 调用 LLM（无工具）
- condition: 条件分支（支持 ==/!=/>/</>=/<=/contains/in 运算符）
- send_message: 通过 ChannelService 发送消息
- hitl: 暂停等待人工确认
- parallel: 并行执行多分支（asyncio.gather，Phase B5）
- agent_call: 调用 AgentService 启动子 Agent 任务（Phase B5）
- sub_workflow: 递归调用 WorkflowService 执行子工作流（Phase B5）

执行模型：
- 节点按 next 列表推进
- condition 节点根据 config.expr + config.op 求值选择分支
- hitl 节点暂停执行，等待外部 confirm 后继续
- parallel 节点汇聚所有分支结果

架构说明：
  本模块位于 Domain 层，仅保留 DAG 推进骨架（execute_node + WorkflowDefinition）。
  节点执行器在 node_executors.py 中，通过函数内延迟导入 app.service.* 调用 Service 层。
"""
from app.domain.workflow.node_executors import (
    eval_condition_advanced,
    exec_agent_call,
    exec_hitl,
    exec_llm_call,
    exec_parallel,
    exec_send_message,
    exec_skill_call,
    exec_sub_workflow,
)
from app.utils.logger import get_logger
from app.utils.response import AppError, ErrorCode

log = get_logger("workflow_engine")

# 支持的节点类型
NODE_TYPES = {
    "start", "end", "skill_call", "llm_call", "condition",
    "send_message", "hitl", "parallel", "agent_call", "sub_workflow",
}


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

    context 包含 inputs / variables / 历史节点输出。
    节点执行器实现在 node_executors.py，本函数仅做分发。
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
        return await exec_skill_call(config, context, user_id), _next_nodes(node, context)

    if node_type == "llm_call":
        return await exec_llm_call(config, context), _next_nodes(node, context)

    if node_type == "send_message":
        return await exec_send_message(config, context, chat_id), _next_nodes(node, context)

    if node_type == "condition":
        next_ids = eval_condition_advanced(node, config, context)
        return {"branched_to": next_ids}, next_ids

    if node_type == "hitl":
        # 暂停，由 WorkflowService 处理 confirm 后再推进
        return await exec_hitl(config, context), []

    # Phase B5: parallel / agent_call / sub_workflow
    if node_type == "parallel":
        output = await exec_parallel(node, context, chat_id, user_id)
        return output, _next_nodes(node, context)

    if node_type == "agent_call":
        output = await exec_agent_call(config, context, chat_id, user_id)
        return output, _next_nodes(node, context)

    if node_type == "sub_workflow":
        output = await exec_sub_workflow(config, context, chat_id, user_id)
        return output, _next_nodes(node, context)

    log.warning("Unknown node type, skip: {}", node_type)
    return {}, _next_nodes(node, context)


def _next_nodes(node: dict, context: dict) -> list[str]:
    """默认下一节点（基于邻接表）"""
    return context.get("__adj", {}).get(node["id"], [])
