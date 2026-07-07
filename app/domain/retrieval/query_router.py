"""QueryRouter - Adaptive RAG 意图分类路由器

Phase 4.1：agent 自主决定检索时机/检索什么。
复用 llm_json_call 做 LLM structured output 意图分类，
LLM 失败时降级为关键词规则路由（保证可用性）。

意图映射（[DR-3.2] Adaptive RAG + [DR-3.4] 4 节点架构）：
- direct：常识/闲聊/简单问题 → 不检索（降本 30%）
- knowledge：政策/文档/非结构化知识 → 知识库检索
- memory：用户偏好/历史上下文 → 记忆检索
- tool：需要调用工具 → 工具检索
"""
from typing import Optional

from app.utils.llm_structured import llm_json_call
from app.utils.logger import get_logger

log = get_logger("query_router")

_SYSTEM_PROMPT = """你是 Agentic RAG 查询路由器。给定用户查询，判断最合适的检索意图。

输出 JSON 格式：
{"intent": "direct|knowledge|memory|tool", "reasoning": "1句话理由"}

意图定义：
1. direct — 闲聊/问候/常识问题/简单计算，无需检索外部信息（如"你好"、"1+1=?"）
2. knowledge — 查询政策/文档/规范/产品说明等非结构化知识（如"报销流程是什么"、"API 文档在哪"）
3. memory — 查询用户偏好/历史交互/个人上下文（如"我之前说过什么"、"我的偏好"）
4. tool — 需要执行操作/调用工具完成任务（如"发邮件给X"、"查询订单状态"、"创建工单"）

规则：
- 不确定时优先 knowledge（宁多检索不漏）
- 同时涉及多类时选最核心的（如"查我的订单"→ tool，非 memory）
- reasoning 简洁，≤20字"""

# 关键词规则 fallback（LLM 失败时用）
# 匹配顺序：memory（强信号）→ tool（强信号）→ direct（闲聊特征）→ knowledge（默认，宁多检索不漏）
_KEYWORD_RULES = {
    "memory": [
        "我之前", "我记得", "我的偏好", "我说过", "上次", "历史",
        "我刚才", "我喜欢", "我过去", "你上次", "还记得", "我记得你",
    ],
    "tool": [
        "发送", "查询", "创建", "删除", "更新", "执行", "调用", "帮我",
        "发邮件", "建工单", "建立", "新建", "备份", "邀请", "快递",
        "工单", "订单", "任务", "记录",
    ],
    "direct": [
        # 问候/感叹
        "你好", "谢谢", "再见", "早安", "晚安", "哈哈", "不错", "有意思",
        # 关于 AI 本身
        "你是", "你叫",
        # 计算/常识
        "等于", "是多少", "乘以", "加上", "减去", "天气",
    ],
}


class QueryRouter:
    """Adaptive RAG 路由器 — LLM 意图分类 + 关键词 fallback"""

    VALID_INTENTS = ("direct", "knowledge", "memory", "tool")

    async def route(
        self,
        query: str,
        context: Optional[dict] = None,
    ) -> str:
        """LLM 意图分类，返回 direct/knowledge/memory/tool

        LLM 失败时降级为关键词规则路由（保证可用性）。
        """
        if not query or not query.strip():
            return "direct"
        # 短查询（≤4字）直接走 direct，省一次 LLM 调用
        if len(query.strip()) <= 4:
            return "direct"

        try:
            result = await llm_json_call(
                _SYSTEM_PROMPT,
                query,
                temperature=0.1,
                max_tokens=150,
                fallback=None,
            )
            intent = result.get("intent", "").strip().lower()
            if intent in self.VALID_INTENTS:
                log.debug("route llm query='{}' -> {} ({})", query[:30], intent, result.get("reasoning", ""))
                return intent
            log.warning("route llm invalid intent: {}, fallback to keyword", intent)
        except Exception as e:
            log.warning("route llm failed: {}, fallback to keyword", e)

        return self._keyword_fallback(query)

    def _keyword_fallback(self, query: str) -> str:
        """关键词规则路由（LLM 失败降级）

        匹配顺序：memory → tool → direct → knowledge（默认）
        memory/tool 是强信号优先匹配；direct 是闲聊特征弱信号；
        都不命中时默认 knowledge（宁多检索不漏）。
        """
        for intent in ("memory", "tool", "direct"):
            for kw in _KEYWORD_RULES[intent]:
                if kw in query:
                    return intent
        # 默认 knowledge（宁多检索不漏）
        return "knowledge"
