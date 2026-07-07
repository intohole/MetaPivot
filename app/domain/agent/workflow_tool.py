"""Agent → Workflow 内置工具

提供两个内置工具让 Agent 在对话中触发工作流：
- trigger_workflow(workflow_id, inputs): 触发工作流异步执行
- list_workflows(): 列出可用工作流（ID + 名称 + 描述，便于 LLM 选择）

设计：
- 不经过 Skill 表（避免污染业务工具），schema 直接注入 available_tools
- 工具执行逻辑在 executor.py 早分支处理（同 finish/delegate_to_subagent）
- 配合 cycle_detector.py 防止 agent→workflow→agent 循环死锁
"""
from app.utils.logger import get_logger

log = get_logger("agent_workflow_tool")

# trigger_workflow 工具：触发工作流执行
TRIGGER_WORKFLOW_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "trigger_workflow",
        "description": (
            "触发已注册的工作流执行。适用于多步骤自动化场景（如：审批流、数据处理 pipeline、通知群发）。"
            "调用前应先用 list_workflows 查看可用工作流，确认 workflow_id。"
            "执行是异步的，返回 execution_id，工作流在后台推进。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "workflow_id": {
                    "type": "string",
                    "description": "工作流 ID（UUID 格式，从 list_workflows 获取）",
                },
                "inputs": {
                    "type": "object",
                    "description": "工作流输入参数（key-value 对）",
                    "default": {},
                },
            },
            "required": ["workflow_id"],
        },
    },
}

# list_workflows 工具：列出可用工作流
LIST_WORKFLOWS_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_workflows",
        "description": (
            "列出当前可用的已启用工作流（ID + 名称 + 描述）。"
            "在调用 trigger_workflow 前先用此工具查看可用工作流列表，确认目标 workflow_id。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}


def get_workflow_tools() -> list[dict]:
    """返回 Agent 内置 workflow 工具 schema 列表

    这些工具不通过 skill_service 注册，而是直接注入 available_tools，
    由 executor.py 的早分支处理（避免与业务 Skill 混淆）。
    """
    return [TRIGGER_WORKFLOW_TOOL_SCHEMA, LIST_WORKFLOWS_TOOL_SCHEMA]


def is_workflow_tool(tool_name: str) -> bool:
    """判断是否为 workflow 内置工具"""
    return tool_name in ("trigger_workflow", "list_workflows")
