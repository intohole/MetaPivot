"""上下文窗口管理 - 防止 messages 无限增长导致 LLM 上下文溢出

核心函数：
- trim_messages：按 token 预算裁剪，保留 system + 最近消息 + 完整 tool_call 对
- summarize_messages：用 LLM 压缩旧消息为摘要（可写入 ChatSummaryORM）

裁剪规则（参考 OpenAI / LangChain 最佳实践）：
1. 始终保留第一条 system 消息
2. assistant 消息含 tool_calls 时，必须与后续所有 tool 结果消息成对保留
   （OpenAI API 强约束：tool_call_id 必须有对应的 tool 消息）
3. 从末尾向前保留消息直到接近预算，超出部分丢弃
4. 丢弃后注入 system 摘要（如有）以保留长对话上下文

调用时机：
- executor_node 每次 LLM 调用前
- replier_node 生成回复前
- 也可由 agent_service._run_task 在加载历史后立即调用

设计原则：
- 纯函数（除 summarize 外），无副作用
- 显式注入 token_counter 依赖（便于测试替换）
"""
from typing import TYPE_CHECKING, Any

from app.utils.logger import get_logger

if TYPE_CHECKING:
    from app.domain.contracts.token_counter import ITokenCounter
    from app.domain.contracts.llm import ILLMProvider

log = get_logger("context_window")

# 保留给工具结果和回复的 token 余量
_SAFE_MARGIN = 512
# 默认上下文窗口（按模型可调整，OpenAI 兼容模型多为 32K/128K）
_DEFAULT_CONTEXT_WINDOW = 32_000


def _is_tool_pair(msg: dict) -> bool:
    """判断是否为 tool 结果消息（必须与 assistant tool_calls 成对保留）"""
    return msg.get("role") == "tool"


def _is_assistant_with_tool_calls(msg: dict) -> bool:
    """判断是否为含 tool_calls 的 assistant 消息"""
    return msg.get("role") == "assistant" and bool(msg.get("tool_calls"))


def trim_messages(
    messages: list[dict],
    max_tokens: int,
    token_counter: "ITokenCounter",
    keep_system: bool = True,
) -> list[dict]:
    """按 token 预算裁剪 messages

    Args:
        messages: OpenAI 格式消息列表
        max_tokens: 允许的最大 token 数（含回复余量）
        token_counter: TokenCounter 实例
        keep_system: 是否保留第一条 system 消息

    Returns:
        裁剪后的消息列表（保持原始顺序）
    """
    if not messages:
        return []

    # 第一步：分离 system 消息（始终保留）
    system_msgs: list[dict] = []
    rest_msgs: list[dict] = []
    if keep_system and messages and messages[0].get("role") == "system":
        system_msgs = [messages[0]]
        rest_msgs = messages[1:]
    else:
        rest_msgs = list(messages)

    # 第二步：从末尾向前累积消息，遇到 tool_call 对时整对保留
    budget = max_tokens - token_counter.count_messages_tokens(system_msgs) - _SAFE_MARGIN
    if budget <= 0:
        # system 消息已超预算，仅保留 system（极端情况）
        log.warning("System message exceeds budget, dropping all other messages")
        return system_msgs

    kept: list[dict] = []
    used = 0
    # 反向遍历，从最新消息向前累积
    i = len(rest_msgs) - 1
    while i >= 0:
        msg = rest_msgs[i]
        msg_tokens = token_counter.count_messages_tokens([msg])

        # 如果是 tool 消息，必须找到 tool_call_id 匹配的 assistant tool_calls 消息一并保留
        if _is_tool_pair(msg):
            # OpenAI API 约束：tool 消息的 tool_call_id 必须在某个 assistant tool_calls 中存在
            # 严格匹配 tool_call_id，避免保留孤立 tool 消息导致 LLM API 报错
            tool_call_id = msg.get("tool_call_id")
            pair_start = -1  # -1 表示未找到配对
            j = i - 1
            while j >= 0:
                prev = rest_msgs[j]
                if _is_assistant_with_tool_calls(prev):
                    call_ids = [tc.get("id") for tc in prev.get("tool_calls", [])]
                    if tool_call_id in call_ids:
                        pair_start = j
                        break
                    # tool_call_id 不匹配，继续向前找（可能有多个 assistant tool_calls 消息）
                j -= 1
            if pair_start < 0:
                # 未找到配对的 assistant tool_calls，丢弃孤立 tool 消息
                # （OpenAI API 要求 tool 消息必须有对应的 tool_calls，否则报错）
                log.debug("dropped orphan tool message: tool_call_id={}", tool_call_id)
                i -= 1
                continue
            # 整对保留 [pair_start, i]（含中间所有消息，保持顺序完整）
            pair_msgs = rest_msgs[pair_start:i + 1]
            pair_tokens = token_counter.count_messages_tokens(pair_msgs)
            if used + pair_tokens > budget:
                break
            kept = pair_msgs + kept
            used += pair_tokens
            i = pair_start - 1
        else:
            if used + msg_tokens > budget:
                break
            kept = [msg] + kept
            used += msg_tokens
            i -= 1

    dropped = len(rest_msgs) - len(kept)
    if dropped > 0:
        log.info(
            "trimmed {} messages: kept {} ({} tokens), dropped {} (budget={})",
            len(rest_msgs), len(kept), used, dropped, budget,
        )

    return system_msgs + kept


async def summarize_messages(
    messages: list[dict],
    llm: "ILLMProvider",
    max_messages: int = 20,
    memory_store: Any = None,
    chat_id: str = "",
) -> str:
    """用 LLM 压缩消息列表为摘要

    长对话场景：超过 max_messages 时把较早的消息压缩为摘要，
    避免无限增长。摘要可存储到 ChatSummaryORM，下次加载时作为 system 上下文。

    Phase B4: 接受 memory_store + chat_id 参数，写入 IMemoryStore.set_summary，
    实现跨任务长对话压缩（对齐 LangMem / Mem0 长对话压缩实践）。

    Args:
        messages: 待压缩的消息列表（oldest → newest）
        llm: LLM Provider 实例
        max_messages: 超过此数量才触发摘要
        memory_store: IMemoryStore 实例（可选，用于持久化摘要）
        chat_id: 会话 ID（可选，配合 memory_store 使用）

    Returns:
        摘要文本（中文，约 200 字以内）；消息数不足时返回空字符串
    """
    if len(messages) < max_messages:
        return ""

    # 取较早的消息（保留最近 10 条原文）
    to_summarize = messages[:-10]
    if not to_summarize:
        return ""

    # 构造对话文本
    dialog_lines: list[str] = []
    for m in to_summarize:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, str):
            dialog_lines.append(f"{role}: {content}")
    dialog_text = "\n".join(dialog_lines)

    summary_prompt = (
        "请将以下对话压缩为简洁摘要（200字以内），保留：\n"
        "1. 用户的关键诉求和已确认的事实（如姓名、订单号、时间）\n"
        "2. 工具调用结果的核心结论\n"
        "3. 未解决的待办事项\n\n"
        f"对话内容：\n{dialog_text}\n\n"
        "摘要："
    )

    try:
        result = await llm.chat_completion(
            messages=[{"role": "user", "content": summary_prompt}],
            temperature=0.0,
        )
        summary = result.get("content", "").strip()
        log.info("summarized {} messages → {} chars", len(to_summarize), len(summary))
        # Phase B4: 持久化到 memory_store（IMemoryStore.set_summary）
        if memory_store and chat_id and summary:
            try:
                await memory_store.set_summary(chat_id, summary)
            except Exception as e:
                log.warning("set_summary failed for {}: {}", chat_id, e)
        return summary
    except Exception as e:
        log.warning("summarize_messages failed: {}", e)
        return ""


def get_context_window_tokens(model: str = "") -> int:
    """根据模型名返回上下文窗口大小（token）"""
    model_lower = model.lower()
    if "gpt-4o" in model_lower or "gpt-4-turbo" in model_lower:
        return 128_000
    if "gpt-4" in model_lower:
        return 8_192
    if "gpt-3.5" in model_lower:
        return 16_385
    if "kimi" in model_lower or "moonshot" in model_lower:
        return 128_000
    if "qwen" in model_lower:
        return 32_768
    if "glm" in model_lower:
        return 128_000
    if "deepseek" in model_lower:
        return 64_000
    return _DEFAULT_CONTEXT_WINDOW


# Sprint 7.3: 执行循环中长对话摘要压缩
_SUMMARY_MARKER = "【对话摘要】"
_SUMMARIZE_THRESHOLD = 30  # 消息数超此阈值时触发摘要压缩


def inject_summary_to_system(state: Any, summary: str) -> None:
    """将摘要注入 system 消息（替换旧摘要，避免重复堆积）

    安全策略：只修改 messages[0] 的 content，不改变 messages 结构，
    避免破坏 tool_call↔tool_result 对（trim_messages 依赖结构完整性）。
    """
    if not state.messages or not summary:
        return
    sys_msg = state.messages[0]
    if sys_msg.get("role") != "system":
        return
    content = sys_msg.get("content", "")
    # 移除旧摘要（如有），避免重复堆积
    if _SUMMARY_MARKER in content:
        content = content.split(_SUMMARY_MARKER)[0].rstrip()
    sys_msg["content"] = content + f"\n\n{_SUMMARY_MARKER}\n{summary}"


async def maybe_summarize_in_execution(state: Any, llm: "ILLMProvider") -> bool:
    """Sprint 7.3: 执行循环中长对话摘要压缩

    消息数超 _SUMMARIZE_THRESHOLD 时，用 LLM 压缩旧消息为摘要并注入 system 消息。
    与任务启动时的 summarize_messages（agent_runner.run_task）互补：
    - 启动时摘要：压缩历史消息（跨任务记忆）
    - 执行中摘要：压缩当前任务累积的消息（防止单任务内上下文溢出）

    Returns:
        True 表示执行了摘要压缩（调用方应 drain 事件到 SSE）
    """
    if len(state.messages) <= _SUMMARIZE_THRESHOLD:
        return False
    try:
        summary = await summarize_messages(
            state.messages, llm, max_messages=_SUMMARIZE_THRESHOLD
        )
        if not summary:
            return False
        inject_summary_to_system(state, summary)
        state.add_event("context_summarized", {
            "message_count": len(state.messages),
            "summary_length": len(summary),
        })
        log.info(
            "In-execution summarization: {} messages → {} chars summary",
            len(state.messages), len(summary),
        )
        return True
    except Exception as e:
        log.warning("In-execution summarization failed: {}", e)
        return False
