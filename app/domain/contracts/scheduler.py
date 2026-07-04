"""IScheduler - 定时任务调度器抽象接口

用于 Agent 解析用户对话中的定时任务后创建调度，到点触发执行。

实现：
- AsyncScheduler：基于 asyncio + DB 轮询，单进程零外部依赖
  适合小企业单机部署（无 Redis/Celery 依赖）
- 预留扩展：CeleryScheduler（集群，beat 调度）/ APScheduler（进程内调度）

接口约束：
- schedule() 创建定时任务，返回 task_id
- cancel() 取消未执行的任务
- list_pending() 查询待执行任务
- start()/stop() 启动/停止后台轮询（lifespan 调用）
- health() 健康检查（readiness endpoint 用）
"""
from datetime import datetime
from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class IScheduler(Protocol):
    """定时任务调度器统一接口"""

    async def schedule(
        self,
        message: str,
        run_at: Optional[datetime] = None,
        recurring: str = "none",
        cron_expr: str = "",
        chat_id: str = "",
        user_id: str = "",
        channel: str = "api",
        context: Optional[dict] = None,
        description: str = "",
    ) -> int:
        """创建定时任务

        Args:
            message: 触发时执行的 message（去掉时间描述后的核心诉求）
            run_at: 一次性任务的执行时间（None + recurring!="none" 时按周期计算）
            recurring: none/daily/weekly/monthly（cron_expr 为空时使用）
            cron_expr: 标准 5 段 cron 表达式（优先于 recurring，如 "0 9 * * 1-5" 工作日 9 点）
            chat_id: 触发源会话 ID（用于回调到原会话）
            user_id: 创建者
            channel: 渠道（api/im_dingtalk/...）
            context: 上下文（传递给执行时的 Agent）
            description: 用户可读描述

        Returns:
            scheduled_task_id
        """
        ...

    async def cancel(self, task_id: int) -> bool:
        """取消未执行的定时任务

        Returns:
            True 取消成功，False 任务不存在或已执行
        """
        ...

    async def list_pending(
        self, user_id: str = "", limit: int = 50
    ) -> list[dict]:
        """查询待执行的定时任务"""
        ...

    async def list_dlq(
        self, user_id: str = "", page: int = 1, page_size: int = 20
    ) -> dict:
        """查询死信队列（retry_count >= max_retries 的 failed 任务）

        Returns:
            {"items": [...], "total": int, "page": int, "page_size": int}
        """
        ...

    async def retry_failed(self, task_id: int, user_id: str = "") -> bool:
        """手动重试失败任务（重置 retry_count=0，状态回 pending，重新入队）

        Returns:
            True 重试成功，False 任务不存在/状态非 failed/无权操作
        """
        ...

    async def start(self) -> None:
        """启动后台轮询任务（lifespan startup 调用）"""
        ...

    async def stop(self) -> None:
        """停止后台轮询任务（lifespan shutdown 调用）"""
        ...

    async def health(self) -> bool:
        """健康检查"""
        ...
