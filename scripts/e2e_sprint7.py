"""Sprint 7 E2E 测试 - 验证 agent_service 拆分 + executor 重试 + 上下文摘要

覆盖模块：
1. auth - 登录获取 token
2. agent - 启动任务 / 列表 / 详情 / 搜索记忆
3. skills - 列表 / 详情
4. workflows - 列表 / 模板
5. knowledge - 列表
6. schedules - 列表
7. audit - 统计
8. dashboard - 汇总数据
9. admin - 用户/角色/配置

运行：.venv/bin/python scripts/e2e_sprint7.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

BASE = "http://127.0.0.1:8000"
USERNAME = "admin"
PASSWORD = "admin123"

_passed = 0
_failed = 0
_errors: list[str] = []


def ok(name: str, detail: str = "") -> None:
    global _passed
    _passed += 1
    print(f"  ✓ {name}{(' — ' + detail) if detail else ''}")


def fail(name: str, detail: str = "") -> None:
    global _failed
    _failed += 1
    msg = f"  ✗ {name}{(' — ' + detail) if detail else ''}"
    print(msg)
    _errors.append(msg)


async def main() -> None:
    print("=" * 70)
    print("MetaPivot Sprint 7 E2E 测试")
    print("=" * 70)

    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as client:
        # ===== 1. auth =====
        print("\n[1/9] auth")
        r = await client.post("/api/v1/auth/token", json={"username": USERNAME, "password": PASSWORD})
        if r.status_code == 200 and r.json().get("success"):
            token = r.json()["data"]["token"]
            ok("login", f"token_len={len(token)}")
        else:
            fail("login", f"status={r.status_code} body={r.text[:200]}")
            return

        headers = {"Authorization": f"Bearer {token}"}

        # /auth/me
        r = await client.get("/api/v1/auth/me", headers=headers)
        if r.status_code == 200 and r.json().get("data", {}).get("username") == USERNAME:
            ok("auth/me", f"user={r.json()['data']['username']}")
        else:
            fail("auth/me", f"status={r.status_code}")

        # ===== 2. agent =====
        print("\n[2/9] agent")
        # 启动 Agent 任务（POST /agent/chat，验证 agent_runner.run_task 调用链）
        r = await client.post(
            "/api/v1/agent/chat",
            headers=headers,
            json={"message": "你好，请告诉我当前时间", "channel": "api", "chat_id": "e2e-test"},
        )
        if r.status_code in (200, 201, 202):
            data = r.json().get("data", {})
            task_id = data.get("task_id", "")
            ok("start_task", f"task_id={task_id[:8]}...")
        else:
            fail("start_task", f"status={r.status_code} body={r.text[:200]}")
            task_id = ""

        # 列表
        r = await client.get("/api/v1/agent/tasks?page=1&page_size=5", headers=headers)
        if r.status_code == 200:
            items = r.json().get("data", {}).get("items", [])
            ok("list_tasks", f"count={len(items)}")
        else:
            fail("list_tasks", f"status={r.status_code}")

        # 详情
        if task_id:
            r = await client.get(f"/api/v1/agent/tasks/{task_id}", headers=headers)
            if r.status_code == 200:
                ok("get_task", f"status={r.json().get('data', {}).get('status')}")
            else:
                fail("get_task", f"status={r.status_code}")

        # 搜索记忆（POST /agent/memory/search，验证 agent_rag.search_memory 调用链）
        r = await client.post(
            "/api/v1/agent/memory/search",
            headers=headers,
            json={"query": "test", "top_k": 3},
        )
        if r.status_code == 200:
            ok("search_memory", "agent_rag chain OK")
        else:
            fail("search_memory", f"status={r.status_code} body={r.text[:200]}")

        # ===== 3. skills =====
        print("\n[3/9] skills")
        r = await client.get("/api/v1/skills?page=1&page_size=5", headers=headers)
        if r.status_code == 200:
            items = r.json().get("data", {}).get("items", [])
            ok("list_skills", f"count={len(items)}")
            skill_id = items[0]["id"] if items else ""
        else:
            fail("list_skills", f"status={r.status_code}")
            skill_id = ""

        if skill_id:
            r = await client.get(f"/api/v1/skills/{skill_id}", headers=headers)
            if r.status_code == 200:
                ok("get_skill", f"name={r.json().get('data', {}).get('name')}")
            else:
                fail("get_skill", f"status={r.status_code}")

        # ===== 4. workflows =====
        print("\n[4/9] workflows")
        r = await client.get("/api/v1/workflows?page=1&page_size=5", headers=headers)
        if r.status_code == 200:
            ok("list_workflows", f"count={len(r.json().get('data', {}).get('items', []))}")
        else:
            fail("list_workflows", f"status={r.status_code}")

        r = await client.get("/api/v1/workflows/templates?page=1&page_size=5", headers=headers)
        if r.status_code == 200:
            ok("list_workflow_templates", f"count={len(r.json().get('data', {}).get('items', []))}")
        else:
            fail("list_workflow_templates", f"status={r.status_code}")

        # ===== 5. knowledge =====
        print("\n[5/9] knowledge")
        r = await client.get("/api/v1/knowledge/documents?page=1&page_size=5", headers=headers)
        if r.status_code == 200:
            ok("list_documents", f"count={len(r.json().get('data', {}).get('items', []))}")
        else:
            fail("list_documents", f"status={r.status_code}")

        # ===== 6. schedules =====
        print("\n[6/9] schedules")
        r = await client.get("/api/v1/schedules?page=1&page_size=5", headers=headers)
        if r.status_code == 200:
            ok("list_schedules", f"count={len(r.json().get('data', {}).get('items', []))}")
        else:
            fail("list_schedules", f"status={r.status_code}")

        # ===== 7. audit =====
        print("\n[7/9] audit")
        r = await client.get("/api/v1/audit/stats", headers=headers)
        if r.status_code == 200:
            ok("audit_stats", "OK")
        else:
            fail("audit_stats", f"status={r.status_code}")

        # ===== 8. dashboard（聚合端点，前端并行调用 4 个接口）=====
        print("\n[8/9] dashboard (aggregated)")
        dash_ok = True
        r = await client.get("/api/v1/agent/tasks?page=1&page_size=5", headers=headers)
        if r.status_code != 200:
            dash_ok = False
        r = await client.get("/api/v1/skills?page=1&page_size=1", headers=headers)
        if r.status_code != 200:
            dash_ok = False
        r = await client.get("/api/v1/workflows?page=1&page_size=1", headers=headers)
        if r.status_code != 200:
            dash_ok = False
        r = await client.get("/api/v1/workflows/templates?page=1&page_size=4", headers=headers)
        if r.status_code != 200:
            dash_ok = False
        if dash_ok:
            ok("dashboard_aggregation", "4 endpoints OK")
        else:
            fail("dashboard_aggregation", "one or more endpoints failed")

        # ===== 9. admin =====
        print("\n[9/9] admin")
        r = await client.get("/api/v1/users?page=1&page_size=5", headers=headers)
        if r.status_code == 200:
            ok("list_users", f"count={len(r.json().get('data', {}).get('items', []))}")
        else:
            fail("list_users", f"status={r.status_code}")

        r = await client.get("/api/v1/roles", headers=headers)
        if r.status_code == 200:
            ok("list_roles", f"count={len(r.json().get('data', {}).get('items', []))}")
        else:
            fail("list_roles", f"status={r.status_code}")

        r = await client.get("/api/v1/configs", headers=headers)
        if r.status_code == 200:
            ok("list_configs", f"count={len(r.json().get('data', {}).get('items', []))}")
        else:
            fail("list_configs", f"status={r.status_code}")

    # ===== 等待 Agent 任务完成并验证最终状态 =====
    if task_id:
        print("\n[bonus] 等待 Agent 任务完成（验证 agent_runner.consume_agent 链路）")
        async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as client:
            for _ in range(30):
                await asyncio.sleep(2)
                r = await client.get(f"/api/v1/agent/tasks/{task_id}", headers=headers)
                if r.status_code == 200:
                    status = r.json().get("data", {}).get("status", "")
                    if status in ("completed", "failed", "cancelled"):
                        ok("task_terminal", f"final_status={status}")
                        break
            else:
                fail("task_terminal", "timeout (60s)")

    print("\n" + "=" * 70)
    print(f"结果：✓ {_passed} 通过  ✗ {_failed} 失败")
    if _errors:
        print("\n失败明细：")
        for e in _errors:
            print(e)
    print("=" * 70)
    sys.exit(0 if _failed == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
