"""LLM 意图分类器 - 替代关键词规则匹配

策略：
1. 若消息含定时关键词 → 调 LLM 解析定时任务，返回 SCHEDULE 模式
2. 若无可用工具 → 直接 pipeline 模式（简单问答）
3. 若有工具 → 调用 LLM 进行结构化意图分类
4. LLM 失败时降级为关键词规则（兜底）

输出：classify_intent 返回三元组 (mode, intent, schedule_result)
schedule_result 非 None 时表示检测到定时任务（mode=SCHEDULE）
"""
import json
from typing import Optional

from app.domain.agent.prompts import INTENT_PROMPT
from app.domain.agent.state import AgentMode
from app.utils.logger import get_logger

log = get_logger("agent_intent")

# 兜底关键词（LLM 不可用时降级使用）
_AGENT_KEYWORDS = ("查询", "获取", "创建", "申请", "审批", "调用", "执行", "发送", "修改", "删除")
_QA_KEYWORDS = ("是什么", "怎么", "如何", "为什么", "什么是", "吗？", "吗?", "？", "?")


async def classify_intent(
    message: str,
    tools: list[dict],
    llm_provider: Optional[object] = None,
) -> tuple[AgentMode, str, Optional[object]]:
    """LLM 意图分类（含定时任务识别）

    Args:
        message: 用户原始消息
        tools: 可用工具列表（OpenAI tools 格式）
        llm_provider: LLM Provider 实例（延迟注入避免循环依赖）

    Returns:
        (mode, intent_description, schedule_result) 三元组
        schedule_result: ScheduleParseResult 或 None，非 None 时表示检测到定时任务
    """
    # 第一步：检测定时任务意图（在所有其他判断之前）
    if llm_provider is not None:
        try:
            from app.domain.agent.schedule_parser import has_schedule_intent, parse_schedule
            if has_schedule_intent(message):
                result = await parse_schedule(message, llm_provider)
                if result.is_scheduled:
                    return AgentMode.SCHEDULE, f"schedule:{result.recurring}", result
        except Exception as e:
            log.warning("schedule parse in classify_intent failed: {}", e)

    # 无工具 → 直接 pipeline
    if not tools:
        return AgentMode.PIPELINE, "qa_no_tools", None

    # 短消息（< 4 字符）且无明显工具意图 → pipeline 快速路径
    if len(message) < 4 and not any(kw in message for kw in _AGENT_KEYWORDS):
        return AgentMode.PIPELINE, "short_qa", None

    # LLM 分类
    if llm_provider is not None:
        try:
            mode, intent = await _llm_classify(message, tools, llm_provider)
            return mode, intent, None
        except Exception as e:
            log.warning("LLM intent classification failed, fallback to keywords: {}", e)

    # 兜底：关键词规则
    return (*_keyword_classify(message), None)


async def _llm_classify(
    message: str,
    tools: list[dict],
    llm_provider: object,
) -> tuple[AgentMode, str]:
    """调用 LLM 进行结构化意图分类"""
    tools_desc = _format_tools(tools)
    prompt = INTENT_PROMPT.format(tools_desc=tools_desc, message=message)

    result = await llm_provider.chat_completion(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,  # 分类任务用低温度保证稳定
        response_format={"type": "json_object"},
        max_tokens=100,
    )

    content = result.get("content", "").strip()
    parsed = json.loads(content)
    mode_str = parsed.get("mode", "agent")
    intent = parsed.get("intent", "unknown")
    confidence = float(parsed.get("confidence", 0.5))

    mode_map = {
        "pipeline": AgentMode.PIPELINE,
        "agent": AgentMode.AGENT,
        "workflow": AgentMode.WORKFLOW,
    }
    mode = mode_map.get(mode_str, AgentMode.AGENT)

    # 低置信度时降级为 agent（保守策略，优先尝试用工具）
    if confidence < 0.4:
        mode = AgentMode.AGENT
        intent = f"{intent}(low_confidence)"

    log.info("Intent classified: mode={} intent={} confidence={}", mode.value, intent, confidence)
    return mode, intent


def _keyword_classify(message: str) -> tuple[AgentMode, str]:
    """关键词兜底分类（LLM 不可用时使用）"""
    if any(kw in message for kw in _AGENT_KEYWORDS):
        return AgentMode.AGENT, "tool_call_keyword"
    if any(kw in message for kw in _QA_KEYWORDS):
        return AgentMode.PIPELINE, "qa_keyword"
    return AgentMode.AGENT, "task_default"


def _format_tools(tools: list[dict]) -> str:
    """格式化工具列表供 LLM 参考"""
    lines = []
    for t in tools[:20]:  # 最多 20 个，避免 prompt 过长
        func = t.get("function", {})
        name = func.get("name", "unknown")
        desc = func.get("description", "")[:80]
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines) if lines else "（无工具）"
