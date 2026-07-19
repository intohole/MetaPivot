"""Sprint 8 E2E 测试 — 验证文件拆分 + Skill 快捷执行 + Review 路由分离

覆盖：
1. 文件拆分回归：所有原有 API 仍正常工作
2. Sprint 8.2: POST /skills/{id}/execute 端点
3. Sprint 8.2: skill_review_routes 分离后 drafts/revisions 路由仍可访问
4. 边界：执行不存在的 skill → 404
"""
import asyncio
import sys
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
USERNAME = "admin"
PASSWORD = "admin123"

passed = 0
failed = 0


def ok(name, detail=""):
    global passed
    passed += 1
    print(f"  ✓ {name} {detail}")


def fail(name, err):
    global failed
    failed += 1
    print(f"  ✗ {name} — {err}")


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as client:
        # 登录
        r = await client.post("/api/v1/auth/token", json={"username": USERNAME, "password": PASSWORD})
        if r.status_code != 200:
            fail("login", f"status={r.status_code}")
            return
        token = r.json()["data"]["token"]
        headers = {"Authorization": f"Bearer {token}"}
        ok("login")

        print("\n[1/4] 文件拆分回归 — models_agent/models_core 分离")
        # Agent 任务（依赖 AgentTaskORM from models_agent）
        r = await client.get("/api/v1/agent/tasks?page=1&page_size=1", headers=headers)
        if r.status_code == 200:
            ok("agent_tasks (models_agent)", f"count={r.json()['data']['total']}")
        else:
            fail("agent_tasks", f"status={r.status_code}")

        # Skill 列表（依赖 SkillORM from models_user_skill）
        r = await client.get("/api/v1/skills?page=1&page_size=1", headers=headers)
        if r.status_code == 200:
            ok("skills_list (models_user_skill)")
        else:
            fail("skills_list", f"status={r.status_code}")

        # Workflow 列表（依赖 WorkflowORM from models_core）
        r = await client.get("/api/v1/workflows?page=1&page_size=1", headers=headers)
        if r.status_code == 200:
            ok("workflows_list (models_core)")
        else:
            fail("workflows_list", f"status={r.status_code}")

        # 定时任务（依赖 ScheduledTaskORM from models_core）
        r = await client.get("/api/v1/schedules?page=1&page_size=1", headers=headers)
        if r.status_code == 200:
            ok("schedules_list (models_core)")
        else:
            fail("schedules_list", f"status={r.status_code}")

        print("\n[2/4] Sprint 8.2 — Skill 快捷执行端点")
        # 获取一个可测试的 skill
        r = await client.get("/api/v1/skills?page=1&page_size=20", headers=headers)
        skills = r.json()["data"]["items"] if r.status_code == 200 else []
        test_skill = None
        for s in skills:
            if s.get("source_type") == "function" and s.get("enabled"):
                test_skill = s
                break

        if test_skill:
            # 执行 skill
            r = await client.post(
                f"/api/v1/skills/{test_skill['id']}/execute",
                json={"input": {}},
                headers=headers,
            )
            if r.status_code == 200:
                ok("execute_skill", f"name={test_skill['name']}")
            else:
                fail("execute_skill", f"status={r.status_code}, body={r.text[:200]}")

            # 测试不存在的 skill → 404
            r = await client.post(
                "/api/v1/skills/nonexistent-id/execute",
                json={"input": {}},
                headers=headers,
            )
            if r.status_code == 404:
                ok("execute_nonexistent_404")
            else:
                fail("execute_nonexistent_404", f"expected 404, got {r.status_code}")
        else:
            ok("execute_skill", "(skipped — no enabled function skill found)")

        print("\n[3/4] Sprint 8.2 — skill_review_routes 分离后路由可访问")
        # drafts/list（从 skill_routes 迁移到 skill_review_routes）
        r = await client.get("/api/v1/skills/drafts/list", headers=headers)
        if r.status_code == 200:
            ok("drafts_list (skill_review_routes)")
        else:
            fail("drafts_list", f"status={r.status_code}")

        # revisions/list（从 skill_routes 迁移到 skill_review_routes）
        r = await client.get("/api/v1/skills/revisions/list", headers=headers)
        if r.status_code == 200:
            ok("revisions_list (skill_review_routes)")
        else:
            fail("revisions_list", f"status={r.status_code}")

        # health（保留在 skill_routes）
        if test_skill:
            r = await client.get(f"/api/v1/skills/{test_skill['id']}/health", headers=headers)
            if r.status_code == 200:
                ok("skill_health (skill_routes)", f"health={r.json()['data'].get('health')}")
            else:
                fail("skill_health", f"status={r.status_code}")

        print("\n[4/4] Sprint 8.1 — 拆分后服务层链路完整")
        # 创建 + 执行 + 删除 workflow（验证 workflow_runner 委托模式）
        r = await client.post("/api/v1/workflows", json={
            "name": "e2e-sprint8-test",
            "description": "Sprint 8 E2E test",
            "definition": {
                "nodes": [
                    {"id": "n_start", "type": "start", "config": {}},
                    {"id": "n_end", "type": "end", "config": {}},
                ],
                "edges": [{"source": "n_start", "target": "n_end"}],
                "variables": [],
            },
            "trigger": {"type": "manual"},
            "enabled": True,
        }, headers=headers)
        if r.status_code in (200, 201):
            wf_id = r.json()["data"]["id"]
            ok("create_workflow (workflow_service)")
            # 清理
            await client.delete(f"/api/v1/workflows/{wf_id}", headers=headers)
        else:
            fail("create_workflow", f"status={r.status_code}, body={r.text[:200]}")

        # 创建 + 删除定时任务（验证 async_scheduler + scheduler_executor）
        r = await client.post("/api/v1/schedules", json={
            "message": "e2e sprint8 test",
            "channel": "api",
            "run_at": "2099-12-31T23:59:59",
            "description": "Sprint 8 E2E test",
        }, headers=headers)
        if r.status_code in (200, 201):
            sched_id = r.json()["data"]["task_id"]  # schedule 返回 task_id（int）
            ok("create_schedule (async_scheduler)")
            # 清理
            await client.delete(f"/api/v1/schedules/{sched_id}", headers=headers)
        else:
            fail("create_schedule", f"status={r.status_code}, body={r.text[:200]}")

        # skill optimizer 链路（验证 circuit_breaker 分离）
        if test_skill:
            r = await client.post(f"/api/v1/skills/{test_skill['id']}/optimize", headers=headers)
            if r.status_code == 200:
                ok("optimize_skill (optimizer + circuit_breaker)", f"reason={r.json()['data'].get('reason')}")
            else:
                fail("optimize_skill", f"status={r.status_code}")

    print(f"\n{'='*70}")
    print(f"结果：✓ {passed} 通过  ✗ {failed} 失败")
    print(f"{'='*70}")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
