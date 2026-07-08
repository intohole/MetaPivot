"""AuditService - 审计日志记录与查询

职责：
1. 业务事件审计（Skill 调用 / Agent 任务 / 工作流执行 / IM 消息）
2. 分页查询日志
3. 统计聚合（按天/用户/Skill）
4. 数据脱敏（输入哈希，输出摘要）

数据保留：审计日志保留 180 天（配置项 audit.retention_days）。
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import Float, delete, func, select

from app.infra.db.models_core import AuditLogORM
from app.infra.db.session import get_db_session
from app.utils.logger import get_logger
from app.utils.security import desensitize, sha256_hash

log = get_logger("audit_service")


class AuditService:
    """审计服务单例"""

    async def log_action(
        self,
        user_id: str,
        action: str,
        skill_id: Optional[str] = None,
        workflow_id: Optional[str] = None,
        task_id: Optional[str] = None,
        input_data: Optional[dict] = None,
        output_data: Optional[dict] = None,
        duration_ms: Optional[int] = None,
        status: str = "success",
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        ip_address: Optional[str] = None,
        request_id: str = "",
    ) -> None:
        """记录审计日志（异步非阻塞，失败不影响主流程）"""
        try:
            input_hash = sha256_hash(str(input_data)) if input_data else ""
            output_summary = self._summarize_output(output_data)
            async with get_db_session() as session:
                session.add(AuditLogORM(
                    request_id=request_id or "",
                    user_id=user_id or None,
                    action=action,
                    skill_id=skill_id,
                    workflow_id=workflow_id,
                    task_id=task_id,
                    input_hash=input_hash,
                    output_summary=output_summary,
                    duration_ms=duration_ms,
                    status=status,
                    error_code=error_code,
                    error_message=error_message,
                    ip_address=ip_address,
                ))
        except Exception as e:
            log.exception("Write audit log failed: {}", e)

    def _summarize_output(self, output_data: Optional[dict]) -> str:
        """输出脱敏摘要（前200字符，脱敏手机号/身份证等）"""
        if not output_data:
            return ""
        text = str(output_data)
        if len(text) > 200:
            text = text[:200] + "..."
        return desensitize(text)

    async def list_logs(
        self,
        page: int = 1,
        page_size: int = 20,
        user_id: str = "",
        action: str = "",
        skill_id: str = "",
        start_time: str = "",
        end_time: str = "",
    ) -> tuple[list[dict], int]:
        """分页查询审计日志"""
        async with get_db_session() as session:
            stmt = select(AuditLogORM)
            if user_id:
                stmt = stmt.where(AuditLogORM.user_id == user_id)
            if action:
                stmt = stmt.where(AuditLogORM.action == action)
            if skill_id:
                stmt = stmt.where(AuditLogORM.skill_id == skill_id)
            if start_time:
                stmt = stmt.where(AuditLogORM.created_at >= self._parse_time(start_time))
            if end_time:
                stmt = stmt.where(AuditLogORM.created_at <= self._parse_time(end_time))
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = (await session.execute(count_stmt)).scalar() or 0
            stmt = stmt.order_by(AuditLogORM.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
            items = (await session.execute(stmt)).scalars().all()
            return [self._to_dict(log) for log in items], total

    async def stats(self, start_time: str, end_time: str, group_by: str = "day") -> dict:
        """统计聚合"""
        start = self._parse_time(start_time) if start_time else datetime.now() - timedelta(days=30)
        end = self._parse_time(end_time) if end_time else datetime.now()

        # 按天聚合：SQLite 用 func.date()，PostgreSQL 用 date_trunc
        from app.utils.config import settings
        if group_by == "day":
            if settings.db_backend == "sqlite":
                date_expr = func.date(AuditLogORM.created_at).label("key")
            else:
                date_expr = func.date_trunc("day", AuditLogORM.created_at).label("key")
        elif group_by == "user":
            date_expr = AuditLogORM.user_id.label("key")
        elif group_by == "skill":
            date_expr = AuditLogORM.skill_id.label("key")
        else:
            date_expr = func.date(AuditLogORM.created_at).label("key") if settings.db_backend == "sqlite" else func.date_trunc("day", AuditLogORM.created_at).label("key")

        async with get_db_session() as session:
            stmt = (
                select(
                    date_expr,
                    func.count().label("count"),
                    func.avg(func.cast(AuditLogORM.duration_ms, Float)).label("avg_duration"),
                )
                .where(AuditLogORM.created_at.between(start, end))
                .group_by(date_expr)
                .order_by(date_expr)
            )
            result = await session.execute(stmt)
            stats_list = []
            for row in result:
                stats_list.append({
                    "key": str(row.key),
                    "count": row.count,
                    "avg_duration_ms": int(row.avg_duration) if row.avg_duration else 0,
                })
            return {"stats": stats_list, "total": sum(s["count"] for s in stats_list)}

    async def cleanup_expired(self, retention_days: int = 180) -> int:
        """清理过期审计日志，返回删除条数"""
        cutoff = datetime.now() - timedelta(days=retention_days)
        async with get_db_session() as session:
            result = await session.execute(
                delete(AuditLogORM).where(AuditLogORM.created_at < cutoff)
            )
            log.info("Cleaned up {} expired audit logs", result.rowcount)
            return result.rowcount

    def _parse_time(self, time_str: str) -> datetime:
        """解析时间字符串（ISO8601 或 'YYYY-MM-DD'）"""
        try:
            return datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        except ValueError:
            return datetime.strptime(time_str, "%Y-%m-%d")

    def _to_dict(self, log: AuditLogORM) -> dict:
        return {
            "id": log.id,
            "request_id": log.request_id,
            "user_id": log.user_id,
            "action": log.action,
            "skill_id": log.skill_id,
            "workflow_id": log.workflow_id,
            "task_id": log.task_id,
            "input_hash": log.input_hash,
            "output_summary": log.output_summary,
            "duration_ms": log.duration_ms,
            "status": log.status,
            "error_code": log.error_code,
            "ip_address": log.ip_address,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }


audit_service = AuditService()

