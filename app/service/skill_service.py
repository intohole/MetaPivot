"""SkillService - Skill 注册、查询、执行、给 Agent 提供 LLM 工具列表

职责：
1. Skill CRUD（持久化到 PostgreSQL）
2. 按 source_type 路由执行：function → call_function / mcp → mcp_client / workflow → workflow_service
3. 生成 LLM 可用的 tools 列表（OpenAI Function Call 格式）
4. 调用计数与审计

依赖方向：Service → Infra（MCPClient/call_function）+ Data（ORM）
"""
import asyncio
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select

from app.infra.db.models_user_skill import SkillORM
from app.infra.db.session import get_db_session
from app.utils.logger import get_logger
from app.utils.metrics import record_skill_call
from app.utils.response import AppError, ErrorCode

log = get_logger("skill_service")

# 单值截断阈值：超过则截断为 prefix…（避免 args_summary 膨胀 DB）
_ARG_MAX_LEN = 200


def _safe_args_summary(args: dict) -> dict:
    """脱敏 + 截断 args，生成可持久化的入参摘要

    - 仅保留 JSON 可序列化类型（dict/list/str/num/bool/None）
    - 单个字符串值超过 _ARG_MAX_LEN 截断
    - 敏感键（password/token/secret/api_key）脱敏为 ***
    """
    if not isinstance(args, dict):
        return {"_note": "non_dict_args", "type": type(args).__name__}
    sensitive = {"password", "token", "secret", "api_key", "apikey", "authorization", "cookie"}

    def _sanitize(v):
        if isinstance(v, dict):
            return {k: ("***" if k.lower() in sensitive else _sanitize(vv)) for k, vv in v.items()}
        if isinstance(v, list):
            return [_sanitize(x) for x in v[:10]]  # 仅保留前 10 项
        if isinstance(v, str):
            return v[:_ARG_MAX_LEN] + "…" if len(v) > _ARG_MAX_LEN else v
        if isinstance(v, (int, float, bool)) or v is None:
            return v
        return str(v)[:_ARG_MAX_LEN]  # 兜底转字符串

    try:
        return {k: ("***" if k.lower() in sensitive else _sanitize(v)) for k, v in args.items()}
    except Exception:
        return {"_note": "sanitize_failed"}


async def _record_execution_safe(**kwargs) -> None:
    """fire-and-forget 包装：记录 Skill 执行结果，异常仅记日志不影响主流程"""
    try:
        from app.domain.skill.optimizer import record_execution
        await record_execution(**kwargs)
    except Exception as e:
        log.debug("Record execution failed (non-critical): {}", e)


class SkillService:
    """Skill 服务单例"""

    # 工具名黑名单前缀（避免 LLM 误调用内部工具）
    _RESERVED_PREFIX = "_"

    async def create_skill(self, data: dict, owner_id: str = "") -> dict:
        """创建 Skill（Phase 3: owner_id 注入创建者）"""
        async with get_db_session() as session:
            exists = await session.execute(select(SkillORM).where(SkillORM.name == data["name"]))
            if exists.scalar_one_or_none():
                raise AppError(ErrorCode.VALIDATION_ERROR, "Skill 名称已存在", 409)
            self._validate(data)
            skill = SkillORM(**data)
            if owner_id:
                skill.owner_id = owner_id
            session.add(skill)
            await session.flush()
            log.info("Skill created: {} ({})", skill.name, skill.source_type)
            return self._to_dict(skill)

    async def list_skills(
        self,
        page: int = 1,
        page_size: int = 20,
        enabled: Optional[bool] = None,
        source_type: Optional[str] = None,
        keyword: str = "",
        owner_id: str = "",
        scope: str = "all",  # all/my/team
    ) -> tuple[list[SkillORM], int]:
        """分页查询（Phase 3: scope 过滤 my/team/all）"""
        async with get_db_session() as session:
            stmt = select(SkillORM)
            if scope == "my" and owner_id:
                stmt = stmt.where(SkillORM.owner_id == owner_id)
            elif scope == "team":
                stmt = stmt.where(SkillORM.visibility == "shared")
            if enabled is not None:
                stmt = stmt.where(SkillORM.enabled == enabled)
            if source_type:
                stmt = stmt.where(SkillORM.source_type == source_type)
            if keyword:
                stmt = stmt.where(SkillORM.name.ilike(f"%{keyword}%"))
            count_stmt = select(func.count()).select_from(stmt.subquery())
            total = (await session.execute(count_stmt)).scalar() or 0
            stmt = stmt.order_by(SkillORM.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
            items = (await session.execute(stmt)).scalars().all()
            return items, total

    async def get_skill(self, skill_id: str) -> SkillORM:
        async with get_db_session() as session:
            skill = await session.get(SkillORM, skill_id)
            if skill is None:
                raise AppError(ErrorCode.SKILL_NOT_FOUND, status_code=404)
            return skill

    async def update_skill(self, skill_id: str, update_data: dict) -> dict:
        async with get_db_session() as session:
            skill = await session.get(SkillORM, skill_id)
            if skill is None:
                raise AppError(ErrorCode.SKILL_NOT_FOUND, status_code=404)
            for k, v in update_data.items():
                if hasattr(skill, k) and v is not None:
                    setattr(skill, k, v)
            await session.flush()
            # onupdate=func.now() 的属性在 flush 后需 refresh 才能访问，否则触发 greenlet lazy-load 错误
            await session.refresh(skill)
            return {"id": skill.id, "updated_at": skill.updated_at.isoformat() if skill.updated_at else None}

    async def delete_skill(self, skill_id: str) -> dict:
        async with get_db_session() as session:
            skill = await session.get(SkillORM, skill_id)
            if skill is None:
                raise AppError(ErrorCode.SKILL_NOT_FOUND, status_code=404)
            await session.delete(skill)
            return {"id": skill_id, "deleted": True}

    async def set_enabled(self, skill_id: str, enabled: bool) -> dict:
        async with get_db_session() as session:
            skill = await session.get(SkillORM, skill_id)
            if skill is None:
                raise AppError(ErrorCode.SKILL_NOT_FOUND, status_code=404)
            skill.enabled = enabled
            await session.flush()
            return {"id": skill.id, "enabled": skill.enabled}

    async def publish_to_team(self, skill_id: str, user_id: str) -> dict:
        """Phase 3: 个人 skill 发布为团队 shared（private→shared, version+1, changelog 追加）"""
        async with get_db_session() as session:
            skill = await session.get(SkillORM, skill_id)
            if skill is None:
                raise AppError(ErrorCode.SKILL_NOT_FOUND, status_code=404)
            if skill.owner_id and skill.owner_id != user_id:
                raise AppError(ErrorCode.AUTH_PERMISSION_DENIED, "仅 owner 可发布", 403)
            skill.visibility = "shared"
            skill.version += 1
            skill.changelog = [*skill.changelog, {
                "version": skill.version, "change": "publish_to_team",
                "at": datetime.now().isoformat(),
            }]
            await session.flush()
            log.info("Skill published to team: {} v{}", skill.name, skill.version)
            return {"id": skill.id, "visibility": "shared", "version": skill.version}

    async def create_skill_from_workflow(self, workflow_id, name, description, owner_id="", tags=None):
        from app.domain.skill.recorder import create_skill_from_workflow as _impl
        return await _impl(workflow_id, name, description, owner_id, tags)

    async def record_task_to_skill(self, task_id, name, description, owner_id="", tags=None):
        from app.domain.skill.recorder import record_task_to_skill as _impl
        return await _impl(task_id, name, description, owner_id, tags)

    # ============ 执行 ============

    async def execute(self, skill_id: str, args: dict, user_id: str = "") -> dict:
        """执行 Skill，按 source_type 路由"""
        skill = await self.get_skill(skill_id)
        if not skill.enabled:
            raise AppError(ErrorCode.SKILL_DISABLED, status_code=403)

        started = datetime.now()
        result: dict
        try:
            if skill.source_type == "function":
                from app.infra.tools.registry import call_function
                result = await call_function(skill.source_ref, args)
            elif skill.source_type == "mcp":
                from app.infra.mcp.client import mcp_client
                # source_ref 格式：mcp_server_name.tool_name
                parts = skill.source_ref.split(".", 1)
                if len(parts) != 2:
                    raise AppError(ErrorCode.SKILL_EXECUTION_FAILED, "MCP source_ref 格式错误")
                result = await mcp_client.call(parts[0], parts[1], args)
            elif skill.source_type == "workflow":
                from app.service.workflow_service import workflow_service
                wf_result = await workflow_service.execute_workflow(
                    workflow_id=skill.source_ref, inputs=args, user_id=user_id
                )
                result = {"execution_id": wf_result.get("execution_id")}
            else:
                raise AppError(ErrorCode.SKILL_EXECUTION_FAILED, f"未知 source_type: {skill.source_type}")
        except AppError:
            raise
        except Exception as e:
            log.exception("Skill execute failed: {}", skill.name)
            result = {"error": str(e)}

        duration = int((datetime.now() - started).total_seconds() * 1000)
        await self._incr_call_count(skill_id)
        skill_status = "success" if "error" not in result else "failed"
        # 写审计 + 指标采集
        from app.service.audit_service import audit_service
        await audit_service.log_action(
            user_id=user_id, action="skill.call", skill_id=skill_id,
            input_data=args, output_data=result, duration_ms=duration,
            status=skill_status,
        )
        record_skill_call(skill.name, skill_status)
        # Skill 自进化：记录执行结果供 optimizer 分析（fire-and-forget，不阻塞响应）
        asyncio.create_task(_record_execution_safe(
            skill_id=skill_id, skill_name=skill.name, status=skill_status,
            duration_ms=duration, args_summary=_safe_args_summary(args),
            error_message=result.get("error", "") if skill_status == "failed" else "",
        ))
        return result

    async def test_skill(self, skill_id: str, args: dict) -> dict:
        """测试 Skill（不写审计、不增加 call_count，用于管理后台）"""
        from datetime import datetime
        started = datetime.now()
        try:
            result = await self._execute_raw(skill_id, args)
            duration = int((datetime.now() - started).total_seconds() * 1000)
            return {
                "success": "error" not in result,
                "result": result,
                "duration_ms": duration,
                "error": result.get("error"),
            }
        except AppError as e:
            return {"success": False, "error": {"code": e.code, "message": e.message}, "duration_ms": 0}

    async def _execute_raw(self, skill_id: str, args: dict) -> dict:
        """执行 Skill 但不写审计、不增加 call_count（测试用）"""
        skill = await self.get_skill(skill_id)
        if not skill.enabled:
            raise AppError(ErrorCode.SKILL_DISABLED, status_code=403)
        if skill.source_type == "function":
            from app.infra.tools.registry import call_function
            return await call_function(skill.source_ref, args)
        elif skill.source_type == "mcp":
            from app.infra.mcp.client import mcp_client
            parts = skill.source_ref.split(".", 1)
            if len(parts) != 2:
                raise AppError(ErrorCode.SKILL_EXECUTION_FAILED, "MCP source_ref 格式错误")
            return await mcp_client.call(parts[0], parts[1], args)
        elif skill.source_type == "workflow":
            from app.service.workflow_service import workflow_service
            wf_result = await workflow_service.execute_workflow(
                workflow_id=skill.source_ref, inputs=args, user_id="test"
            )
            return {"execution_id": wf_result.get("execution_id")}
        raise AppError(ErrorCode.SKILL_EXECUTION_FAILED, f"未知 source_type: {skill.source_type}")

    # ============ Agent 工具列表 ============

    async def list_tools_for_llm(self, permission: str = "user") -> list[dict]:
        """生成 LLM tools 列表（OpenAI Function Call 格式）

        仅返回 enabled 且权限匹配的 Skill。
        """
        async with get_db_session() as session:
            stmt = select(SkillORM).where(SkillORM.enabled == True)  # noqa: E712
            skills = (await session.execute(stmt)).scalars().all()

        tools: list[dict] = []
        for s in skills:
            if s.name.startswith(self._RESERVED_PREFIX):
                continue
            if not self._permission_allowed(s.permission, permission):
                continue
            tools.append({
                "type": "function",
                "function": {
                    "name": s.name,
                    "description": s.description,
                    "parameters": s.input_schema or {"type": "object", "properties": {}},
                },
                "metadata": {
                    "skill_id": s.id,
                    "source_type": s.source_type,
                    "require_confirm": s.require_confirm,
                },
            })
        return tools

    async def find_skill_id_by_name(self, name: str) -> Optional[str]:
        async with get_db_session() as session:
            stmt = select(SkillORM.id).where(SkillORM.name == name, SkillORM.enabled == True)  # noqa: E712
            return (await session.execute(stmt)).scalar_one_or_none()

    # ============ 内部工具 ============

    def _validate(self, data: dict) -> None:
        if data.get("source_type") not in ("mcp", "function", "workflow"):
            raise AppError(ErrorCode.VALIDATION_ERROR, "source_type 必须为 mcp/function/workflow", 400)
        if not data.get("source_ref"):
            raise AppError(ErrorCode.VALIDATION_ERROR, "source_ref 不能为空", 400)
        if not data.get("input_schema"):
            raise AppError(ErrorCode.VALIDATION_ERROR, "input_schema 不能为空", 400)

    def _permission_allowed(self, skill_perm: str, user_role: str) -> bool:
        """简单权限匹配：admin 可调所有，manager 可调 user/manager 级，user 只能调 user 级"""
        if skill_perm == "user":
            return True
        if skill_perm == "manager" and user_role in ("manager", "admin"):
            return True
        if skill_perm == "admin" and user_role == "admin":
            return True
        return False

    async def _incr_call_count(self, skill_id: str) -> None:
        from sqlalchemy import update
        async with get_db_session() as session:
            await session.execute(
                update(SkillORM)
                .where(SkillORM.id == skill_id)
                .values(call_count=SkillORM.call_count + 1, last_called_at=datetime.now())
            )

    def _to_dict(self, s: SkillORM) -> dict:
        return {
            "id": s.id, "name": s.name, "description": s.description,
            "input_schema": s.input_schema, "source_type": s.source_type,
            "source_ref": s.source_ref, "permission": s.permission,
            "require_confirm": s.require_confirm, "tags": s.tags,
            "enabled": s.enabled, "created_at": s.created_at.isoformat() if s.created_at else None,
            "owner_id": s.owner_id, "visibility": s.visibility,
            "version": s.version, "changelog": s.changelog,
        }


skill_service = SkillService()
