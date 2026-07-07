"""IMemoryStore - 多轮对话记忆存储抽象接口

用于 Agent 跨任务多轮记忆，支持单机/集群两种部署：

- InMemoryMemoryStore：进程内 dict 存储，适合开发环境（零依赖，重启丢失）
- DBMemoryStore：基于 ChatMessageORM 持久化到数据库，适合生产环境
  （SQLite 单机 / PostgreSQL 集群，跟随 DB_BACKEND 自动切换）
- SemanticMemoryStore：在 DBMemoryStore 基础上叠加向量记忆（embedding + 语义召回 + 事实抽取），
  适合需要跨会话语义关联的场景（memory_backend=semantic）

接口约束：
- load_history() 返回最近 N 条消息（role/content/metadata）， oldest → newest 顺序
- append_message() 追加单条消息（不阻塞主流程）
- get_summary()/set_summary() 长对话压缩摘要（避免无限增长）
- clear() 清空会话（GDPR 合规 / 用户重置）
- 语义扩展（默认 no-op，仅 SemanticMemoryStore 实现）：
  - append_with_embedding()：存消息时同时计算 embedding 入向量库
  - search_semantic()：跨会话语义召回相关记忆（用户问"我有什么偏好"时返回偏好事实）
  - consolidate_memories()：Mem0 风格事实抽取（从对话中抽取三元组存为长期记忆）
"""
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class IMemoryStore(Protocol):
    """多轮对话记忆存储统一接口"""

    async def load_history(
        self, chat_id: str, limit: int = 20
    ) -> list[dict]:
        """加载会话历史消息

        Args:
            chat_id: 会话 ID（IM 消息的 chat_id / API 请求的 session_id）
            limit: 最多返回条数（默认 20）

        Returns:
            消息列表，oldest → newest 顺序，每条形如：
            {"role": "user"/"assistant"/"system", "content": "...", "metadata": {...}}
        """
        ...

    async def count_history(self, chat_id: str) -> int:
        """返回会话消息总数（用于触发条件判断，避免 load_history 全量加载）

        _maybe_consolidate 等场景需要知道消息总数判断是否达到 interval 倍数，
        用此方法替代 load_history(limit=大数) + len()，避免加载全部消息内容。
        """
        ...

    async def append_message(
        self,
        chat_id: str,
        role: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """追加单条消息到会话历史

        Args:
            chat_id: 会话 ID
            role: 消息角色（user/assistant/system/tool）
            content: 消息内容
            metadata: 额外元数据（如 tool_call_id, task_id, usage 等）
        """
        ...

    async def get_summary(self, chat_id: str) -> Optional[str]:
        """获取会话的压缩摘要（长对话用）

        Returns:
            摘要文本，无摘要时返回 None
        """
        ...

    async def set_summary(
        self, chat_id: str, summary: str, message_count: int = 0
    ) -> None:
        """更新会话摘要

        Args:
            chat_id: 会话 ID
            summary: 压缩后的摘要文本
            message_count: 生成摘要时的消息数量（用于判断是否需要重新摘要）
        """
        ...

    async def clear(self, chat_id: str) -> None:
        """清空会话所有消息和摘要（GDPR 合规 / 用户重置）"""
        ...

    async def health(self) -> bool:
        """健康检查"""
        ...

    # ============ 语义记忆扩展（默认 no-op，仅 SemanticMemoryStore 实现）============

    async def append_with_embedding(
        self,
        chat_id: str,
        role: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """追加消息 + 计算 embedding 入向量库（语义记忆）

        语义记忆后端（SemanticMemoryStore）实现：
        - 调 ILLMProvider.embed(content) 生成向量
        - 委托 IVectorStore.upsert(agent_memory collection, [{id, vector, content, metadata}])
        - 同时调 append_message 存原文（episodic 记忆，保持单一数据源）

        非 semantic 后端默认 no-op（向后兼容 InMemoryMemoryStore / DBMemoryStore）。
        """
        ...

    async def search_semantic(
        self,
        query: str,
        chat_id: Optional[str] = None,
        top_k: int = 5,
    ) -> list[dict]:
        """跨会话语义召回相关记忆（向量近邻检索）

        用于 Agent 加载历史时补充语义相关记忆：
        - 用户问"我有什么偏好"时，从 agent_memory collection 召回相关事实
        - chat_id 限定单会话，None 跨所有会话语义检索

        Returns:
            [{role, content, score, metadata}]（按相似度降序）
            非 semantic 后端默认返回空列表（不补充语义记忆）。
        """
        ...

    async def consolidate_memories(self, chat_id: str) -> None:
        """Mem0 风格事实抽取（从对话中抽取长期记忆）

        触发时机：每 N 条消息（memory_consolidate_interval）或会话结束时调一次。
        实现：
        - 调 LLM 从最近对话中抽取事实三元组（如用户偏好 / 习惯 / 项目背景）
        - 抽取的事实存为 semantic 记忆（带 metadata.type="fact"），供后续 search_semantic 召回
        - 双阶段设计省 90% token（相比每次召回全量重读对话历史）

        非 semantic 后端默认 no-op。
        """
        ...
