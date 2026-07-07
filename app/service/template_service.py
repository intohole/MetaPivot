"""TemplateService - 工作流模板（SOP）CRUD + 实例化

职责：
1. 模板 CRUD（团队 SOP 沉淀与复用）
2. instantiate_template：基于模板创建 WorkflowORM（usage_count += 1）
3. 模板校验：definition 经 WorkflowDefinition 验证，trigger_template 经 parse_trigger 验证

依赖方向：service → domain(workflow.engine/trigger_spec) + data(ORM)，符合分层约束。
"""
from typing import Optional

from sqlalchemy import func, select

from app.domain.workflow.engine import WorkflowDefinition
from app.domain.workflow.trigger_spec import parse_trigger
from app.infra.db.models_core import WorkflowORM, WorkflowTemplateORM
from app.infra.db.session import get_db_session
from app.utils.logger import get_logger
from app.utils.response import AppError, ErrorCode

log = get_logger("template_service")


class TemplateService:
    """工作流模板服务单例"""

    # ============ 查询 ============

    async def list_templates(
        self,
        page: int = 1,
        page_size: int = 20,
        category: str = "",
        keyword: str = "",
    ) -> tuple[list[WorkflowTemplateORM], int]:
        async with get_db_session() as session:
            stmt = select(WorkflowTemplateORM)
            if category:
                stmt = stmt.where(WorkflowTemplateORM.category == category)
            if keyword:
                like = f"%{keyword}%"
                stmt = stmt.where(
                    WorkflowTemplateORM.name.ilike(like)
                    | WorkflowTemplateORM.description.ilike(like)
                )
            total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
            stmt = stmt.order_by(WorkflowTemplateORM.usage_count.desc(), WorkflowTemplateORM.created_at.desc()) \
                .offset((page - 1) * page_size).limit(page_size)
            items = (await session.execute(stmt)).scalars().all()
            return items, total

    async def get_template(self, template_id: str) -> WorkflowTemplateORM:
        async with get_db_session() as session:
            tpl = await session.get(WorkflowTemplateORM, template_id)
            if tpl is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "模板不存在", 404)
            return tpl

    # ============ 写操作 ============

    async def create_template(self, data: dict, created_by: str = "") -> dict:
        """创建模板（管理员沉淀 SOP）"""
        WorkflowDefinition(data["definition"])  # 校验 DAG 合法性
        parse_trigger(data.get("trigger_template") or {"type": "manual"})  # 校验触发器合法性
        async with get_db_session() as session:
            tpl = WorkflowTemplateORM(
                name=data["name"],
                description=data.get("description", ""),
                category=data.get("category", "general"),
                definition=data["definition"],
                trigger_template=data.get("trigger_template", {}),
                input_schema=data.get("input_schema", {}),
                tags=data.get("tags", []),
                visibility=data.get("visibility", "public"),
                created_by=created_by or None,
            )
            session.add(tpl)
            await session.flush()
            log.info("Template created: {} ({})", tpl.name, tpl.id)
            return {"id": tpl.id, "name": tpl.name, "status": "created"}

    async def update_template(self, template_id: str, update_data: dict) -> dict:
        async with get_db_session() as session:
            tpl = await session.get(WorkflowTemplateORM, template_id)
            if tpl is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "模板不存在", 404)
            if "definition" in update_data:
                WorkflowDefinition(update_data["definition"])
            if "trigger_template" in update_data:
                parse_trigger(update_data["trigger_template"])  # 校验触发器合法性
            for k, v in update_data.items():
                if hasattr(tpl, k) and v is not None:
                    setattr(tpl, k, v)
            await session.flush()
            return {"id": tpl.id, "updated_at": tpl.updated_at.isoformat() if tpl.updated_at else None}

    async def delete_template(self, template_id: str) -> dict:
        async with get_db_session() as session:
            tpl = await session.get(WorkflowTemplateORM, template_id)
            if tpl is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "模板不存在", 404)
            await session.delete(tpl)
            return {"id": template_id, "deleted": True}

    # ============ 实例化（核心）============

    async def instantiate_template(
        self,
        template_id: str,
        name: str = "",
        trigger_overrides: Optional[dict] = None,
        user_id: str = "",
    ) -> dict:
        """基于模板创建工作流实例

        trigger_overrides: 覆盖模板的 trigger_template（如用户自定义 cron_expr / webhook）
        注：工作流输入参数在执行时通过 POST /workflows/{id}/execute 提供，创建时不持久化默认输入。
        """
        tpl = await self.get_template(template_id)

        # 合并触发器：模板默认 + 用户覆盖
        trigger_dict = dict(tpl.trigger_template or {})
        if trigger_overrides:
            trigger_dict.update(trigger_overrides)
        trigger_spec = parse_trigger(trigger_dict or {"type": "manual"})
        trigger_final = trigger_spec.to_dict()

        wf_name = name or f"{tpl.name} (实例)"
        async with get_db_session() as session:
            wf = WorkflowORM(
                name=wf_name,
                description=tpl.description or "",
                definition=tpl.definition,
                trigger=trigger_final,
                enabled=True,
                created_by=user_id or None,
            )
            session.add(wf)
            # 模板使用计数 +1：原子 UPDATE 避免并发丢更新（read-modify-write 竞态）
            from sqlalchemy import update
            await session.execute(
                update(WorkflowTemplateORM)
                .where(WorkflowTemplateORM.id == template_id)
                .values(usage_count=WorkflowTemplateORM.usage_count + 1)
            )
            await session.flush()

            # webhook 类型自动创建关联 WebhookORM（与 create_workflow 对称）
            if trigger_spec.type == "webhook":
                try:
                    from app.service.webhook_service import webhook_service
                    hook = await webhook_service.create_webhook(
                        name=f"workflow:{wf.name}", target_type="workflow",
                        target_id=wf.id, created_by=user_id,
                    )
                    trigger_final["webhook_token"] = hook["token"]
                    wf.trigger = trigger_final
                    await session.flush()
                except Exception as e:
                    log.warning("auto-create webhook for instantiated workflow {} failed: {}", wf.id, e)

            log.info("Template {} instantiated as workflow {} ({})", template_id, wf_name, wf.id)
            return {
                "id": wf.id, "name": wf.name, "template_id": template_id,
                "trigger": trigger_final, "status": "instantiated",
            }


template_service = TemplateService()
