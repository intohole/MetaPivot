"""Agent 内置工具定义 - finish / delegate_to_subagent

设计：
- finish(summary)：L1 终止条件，LLM 显式标记任务完成
- delegate_to_subagent(message, max_steps)：子代理委托，独立上下文执行复杂子任务

仅声明工具 schema 给 LLM，实际执行逻辑在 executor.py 早分支处理
（避免污染 skill_service 的业务工具列表）
"""

# finish 工具：LLM 显式标记任务完成
FINISH_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "finish",
        "description": (
            "任务完成时调用此工具。当已收集到足够信息可以回答用户问题时，"
            "用此工具输出最终回复摘要，Agent 将立即结束。"
            "步数接近上限时也应主动调用此工具收尾。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "最终回复给用户的摘要内容",
                },
            },
            "required": ["summary"],
        },
    },
}

# delegate_to_subagent 工具：委托子代理执行复杂子任务
DELEGATE_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delegate_to_subagent",
        "description": (
            "将复杂子任务委托给独立子代理执行。"
            "子代理拥有独立上下文窗口，执行完成后仅返回浓缩结论。"
            "适用于：信息检索汇总、多步骤分析、并行子任务等场景。"
            "嵌套深度上限 2 层，避免递归死锁。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "委托给子代理的任务描述（应为完整、可独立执行的指令）",
                },
                "max_steps": {
                    "type": "integer",
                    "description": "子代理最大执行步数（默认 5，最大 10）",
                    "default": 5,
                },
            },
            "required": ["message"],
        },
    },
}


def get_builtin_tools() -> list[dict]:
    """返回 Agent 内置工具 schema 列表

    这些工具不通过 skill_service 注册，而是直接注入到 available_tools，
    由 executor.py 的早分支处理（避免与业务 Skill 混淆）。
    """
    return [FINISH_TOOL_SCHEMA, DELEGATE_TOOL_SCHEMA]


def is_builtin_tool(tool_name: str) -> bool:
    """判断是否为内置工具"""
    return tool_name in ("finish", "delegate_to_subagent")
