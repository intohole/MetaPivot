"""优雅关闭 - lifespan shutdown 阶段取消在途任务 + 释放资源

设计原则：
- 集中管理三个后台任务表（agent / workflow / channel）
- 取消后等待最多 10s 让 finally 块执行（mark_finished / 状态持久化）
- 不强求 DB 写入（recover_stuck_tasks 会在下次启动兜底）
- K8s 兼容：总耗时 < 25s（terminationGracePeriodSeconds 默认 30s）
"""
import asyncio

from app.utils.logger import get_logger

log = get_logger("shutdown")

# 等待 finally 块完成的最大时长
_DRAIN_TIMEOUT = 10  # 秒


async def _cancel_and_drain(label: str, tasks: list[asyncio.Task]) -> None:
    """取消一批任务并等待 finally 块执行"""
    if not tasks:
        return
    pending = [t for t in tasks if not t.done()]
    if not pending:
        log.info("{}: no pending tasks", label)
        return

    for t in pending:
        t.cancel()
    log.info("{}: cancelled {} in-flight tasks, draining...", label, len(pending))

    try:
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True),
            timeout=_DRAIN_TIMEOUT,
        )
        log.info("{}: drain completed", label)
    except asyncio.TimeoutError:
        still = [t for t in pending if not t.done()]
        log.warning(
            "{}: drain timeout, {} task(s) still running (will be force-killed)",
            label, len(still),
        )


async def graceful_shutdown() -> None:
    """优雅关闭：取消所有在途 Agent / Workflow / Channel 任务

    在 lifespan shutdown 阶段调用，先停 IM 入口（避免新任务进入），
    再取消 agent/workflow 任务，最后由 main 关闭 DB/cache。
    """
    # 1. 停止 IM 渠道（避免新消息触发新任务）
    try:
        from app.service.channel_manager import stop_channels
        await stop_channels()
    except Exception as e:
        log.warning("stop_channels failed: {}", e)

    # 2. 取消 Agent 在途任务
    try:
        from app.service.agent_service import agent_service
        agent_tasks = list(agent_service._running_tasks.values())
        await _cancel_and_drain("agent", agent_tasks)
    except Exception as e:
        log.warning("cancel agent tasks failed: {}", e)

    # 3. 取消 Workflow 在途任务
    try:
        from app.service.workflow_service import workflow_service
        workflow_tasks = list(workflow_service._running_tasks.values())
        await _cancel_and_drain("workflow", workflow_tasks)
    except Exception as e:
        log.warning("cancel workflow tasks failed: {}", e)

    # 4. 取消 Channel 派发的处理任务
    try:
        from app.service.channel_service import channel_service
        channel_tasks = list(channel_service._pending_tasks)
        await _cancel_and_drain("channel", channel_tasks)
    except Exception as e:
        log.warning("cancel channel tasks failed: {}", e)

    # 5. 通知 stream_manager 所有未完成任务的订阅者解除阻塞
    try:
        from app.domain.agent.stream import stream_manager
        # _finished 是已完成任务的 dict，这里只需要解除等待中的订阅者
        # 通过 publish 一个 error 事件 + mark_finished 让 SSE 端点退出循环
        for task_id in list(stream_manager._subscribers.keys()):
            try:
                await stream_manager.publish(task_id, {
                    "type": "error",
                    "data": {"code": "SERVER_SHUTDOWN", "message": "服务正在关闭"},
                })
                stream_manager.mark_finished(task_id)
            except Exception:
                pass
    except Exception as e:
        log.warning("notify stream subscribers failed: {}", e)
