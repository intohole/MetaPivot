"""Skill 执行器 — 按 source_type 路由执行 + 测试 + 审计记录

Sprint 8.1: 从 skill_service.py 拆离，保持 skill_service.py ≤ 300 行。
职责：
- execute: 执行 Skill（function/mcp/workflow 三种 source_type 路由）
- test_skill: 测试 Skill（不写审计、不增 call_count）
- _execute_raw: 底层执行（无副作用，测试用）

设计：模块级函数，接受 svc（SkillService 实例）以访问 get_skill/_incr_call_count。
参照 agent_runner.py 的 svc 委托模式。
"""
from datetime import datetime
from typing import TYPE_CHECKING

from app.utils.logger import get_logger
from app.utils.metrics import record_skill_call
from app.utils.response import AppError, ErrorCode

if TYPE_CHECKING:
    from app.service.skill_service import SkillService

log = get_logger("skill_executor")


async def execute(svc: "SkillService", skill_id: str, args: dict, user_id: str = "", tenant_id: str = "default") -> dict:
    """执行 Skill，按 source_type 路由

    Sprint 8.1: 从 SkillService.execute 迁移为模块级函数。
    Sprint 13: tenant_id 用于归属校验 + 审计隔离。
    """
    import asyncio
    from app.service.skill_service import _safe_args_summary, _record_execution_safe

    skill = await svc.get_skill(skill_id, tenant_id=tenant_id)
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
    await svc._incr_call_count(skill_id)
    skill_status = "success" if "error" not in result else "failed"
    # 写审计 + 指标采集
    from app.service.audit_service import audit_service
    await audit_service.log_action(
        user_id=user_id, action="skill.call", skill_id=skill_id,
        input_data=args, output_data=result, duration_ms=duration,
        status=skill_status, tenant_id=tenant_id,
    )
    record_skill_call(skill.name, skill_status)
    # Skill 自进化：记录执行结果供 optimizer 分析（fire-and-forget）
    asyncio.create_task(_record_execution_safe(
        skill_id=skill_id, skill_name=skill.name, status=skill_status,
        duration_ms=duration, args_summary=_safe_args_summary(args),
        error_message=result.get("error", "") if skill_status == "failed" else "",
    ))
    return result


async def test_skill(svc: "SkillService", skill_id: str, args: dict) -> dict:
    """测试 Skill（不写审计、不增加 call_count，用于管理后台）"""
    started = datetime.now()
    try:
        result = await _execute_raw(svc, skill_id, args)
        duration = int((datetime.now() - started).total_seconds() * 1000)
        return {
            "success": "error" not in result,
            "result": result,
            "duration_ms": duration,
            "error": result.get("error"),
        }
    except AppError as e:
        return {"success": False, "error": {"code": e.code, "message": e.message}, "duration_ms": 0}


async def _execute_raw(svc: "SkillService", skill_id: str, args: dict) -> dict:
    """执行 Skill 但不写审计、不增加 call_count（测试用）"""
    skill = await svc.get_skill(skill_id)
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