"""Agent 提示词集中管理

将 system/intent/plan/reflect 提示词从 nodes.py 抽离，便于维护和 A/B 测试。
遵循：节点逻辑（nodes.py）+ 提示词（prompts.py）分离原则。
"""

# ============ 系统提示词 ============
SYSTEM_PROMPT = """你是企业内部办公助手 MetaPivot，帮助员工高效完成工作。
你可以调用已注册的 Skill（包括知识库检索、内部系统 API、MCP 工具等）来解决问题。

规则：
1. 优先使用 Skill 工具获取准确信息，避免凭记忆回答
2. 敏感操作（如审批、删除）会要求用户确认
3. 无法处理时坦诚告知，不要编造信息
4. 回答简洁清晰，使用中文
5. 若工具调用失败，尝试降级处理或告知用户原因"""

# ============ 意图分类提示词 ============
INTENT_PROMPT = """分析用户请求，判断执行模式。

模式说明：
- pipeline: 简单问答，无需工具调用（如"公司是什么时候成立的？"、"你好"）
- agent: 需要调用工具完成任务（如"查询订单状态"、"创建审批"、"帮我发消息"）
- workflow: 明确匹配预定义工作流（如"执行入职流程"、"走报销审批"）

可用工具列表：
{tools_desc}

用户请求：{message}

请输出 JSON（仅 JSON，无其他文字）：
{{"mode": "pipeline|agent|workflow", "intent": "意图简述（10字内）", "confidence": 0.0-1.0}}"""

# ============ 反思提示词 ============
REFLECT_PROMPT = """评估当前工具调用结果是否足以回答用户问题。

用户原始请求：{original_message}
已执行步骤数：{step_count}/{max_steps}
最后工具结果摘要：{last_tool_result}

判断标准：
- "complete": 工具结果已足够回答用户问题
- "continue": 需要更多工具调用（如信息不完整）
- "give_up": 无法完成（工具失败且无替代方案）

输出 JSON：{{"decision": "complete|continue|give_up", "reason": "原因（20字内）"}}"""

# ============ 规划提示词 ============
PLAN_PROMPT = """分析用户请求，制定执行计划。

用户请求：{message}

可用工具：
{tools_desc}

要求：
1. 将复杂任务分解为 1-5 个步骤
2. 每个步骤标注使用的工具名（必须来自可用工具列表）
3. 给出该步骤的目的（10 字内）
4. 若任务简单可直接回答，输出空列表

输出 JSON（仅 JSON）：
{{"plan": [
  {{"step": 1, "tool": "工具名", "purpose": "目的"}},
  ...
]}}"""

# ============ 定时任务解析提示词 ============
SCHEDULE_PARSE_PROMPT = """从用户消息中解析定时任务信息。

当前时间：{now}

用户消息：{message}

判断：
1. 用户是否在指定未来某个时间执行任务？
2. 是一次性的还是周期性的？
   - 周期性优先输出 cron_expr（标准 5 段 cron：分 时 日 月 周，如 "0 9 * * 1-5" 表示工作日 9 点）
   - 一次性输出 run_at（ISO8601 时间，如 2026-07-05T15:00:00+08:00）
3. 触发时要执行的"任务内容"是什么（去掉时间描述后的核心诉求）？

cron_expr 示例：
- "0 9 * * *"        每天 9 点
- "0 9 * * 1-5"      工作日 9 点
- "0 10 * * 0,6"     周末 10 点
- "0 9 * * 1"        每周一 9 点
- "0 9 1 * *"        每月 1 号 9 点
- "*/30 * * * *"     每 30 分钟

输出 JSON（仅 JSON，无定时意图时返回 is_scheduled=false）：
{{
  "is_scheduled": true|false,
  "run_at": "ISO8601 时间（一次性任务必填）",
  "recurring": "none|daily|weekly|monthly（仅当无 cron_expr 时填）",
  "cron_expr": "标准 5 段 cron（周期性任务优先）",
  "task_message": "触发时执行的 message（去掉时间描述，如"查询订单状态"）",
  "description": "用户可读的任务描述（20字内）"
}}"""

# ============ 最终回复提示词 ============
REPLY_PROMPT = """请根据以上对话和工具调用结果，给用户一个清晰、简洁的中文回复。
要点：
1. 直接回答用户问题，不要复述工具调用过程
2. 如有数据，用列表或表格呈现
3. 如工具失败，说明原因并给出建议
4. 保持专业、友好的语气"""


def build_system_prompt(state) -> str:
    """动态构建 system prompt（含资源预算提示，L2 终止条件）

    将当前已用步数/token 注入 system prompt，使 LLM 对资源预算可见，
    在接近上限时主动调用 finish 工具收尾（避免硬性截断）。

    Args:
        state: AgentState 实例

    Returns:
        拼接了资源预算提示的 system prompt
    """
    budget = (
        f"\n\n【资源预算】\n"
        f"- 已用步数: {state.current_step}/{state.max_steps}\n"
        f"- Token 累计: {state.total_tokens}\n"
        f"当步数接近上限时，请调用 finish 工具主动收尾，输出当前已获得的信息摘要。"
    )
    return SYSTEM_PROMPT + budget
