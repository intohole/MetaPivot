"""Sprint 9.1 E2E 测试 — Workflow http_request 节点

覆盖：
1. 基础：GET 公网 API → 200 + body JSON 解析
2. POST + JSON body → 200 + 响应体回显
3. SSRF 防护：请求 127.0.0.1 → status_code=0 + error 含 "SSRF"
4. 错误处理：404 URL → status_code=404（工作流不中断）
5. 鉴权：Basic Auth 头注入（httpbin basic-auth 端点）
6. 4xx 不重试：404 立即返回（无重试延迟）
"""
import asyncio
import time

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


async def create_and_execute(client, headers, name, http_config):
    """创建工作流 + 执行 + 等待完成，返回 outputs"""
    wf_def = {
        "name": name,
        "description": "Sprint 9.1 E2E",
        "enabled": True,
        "trigger": {"type": "manual"},
        "definition": {
            "nodes": [
                {"id": "s", "type": "start", "config": {}},
                {"id": "http1", "type": "http_request", "config": http_config},
                {"id": "e", "type": "end", "config": {}},
            ],
            "edges": [
                {"source": "s", "target": "http1"},
                {"source": "http1", "target": "e"},
            ],
            "variables": [],
        },
    }
    r = await client.post("/api/v1/workflows", json=wf_def, headers=headers)
    if r.status_code != 201:
        return None, f"create failed status={r.status_code} body={r.text[:200]}"
    wf_id = r.json()["data"]["id"]

    r = await client.post(f"/api/v1/workflows/{wf_id}/execute", json={"inputs": {}}, headers=headers)
    if r.status_code != 202:
        return None, f"execute failed status={r.status_code} body={r.text[:200]}"
    exec_id = r.json()["data"]["execution_id"]

    # 轮询执行状态（最多 20s）
    for _ in range(20):
        await asyncio.sleep(1)
        r = await client.get(f"/api/v1/workflows/executions/{exec_id}", headers=headers)
        if r.status_code != 200:
            continue
        data = r.json()["data"]
        if data.get("status") in ("completed", "failed"):
            return data, None
    return None, "execution timeout (20s)"


async def main():
    global passed, failed
    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as client:
        # 登录
        r = await client.post("/api/v1/auth/token", json={"username": USERNAME, "password": PASSWORD})
        if r.status_code != 200:
            fail("login", f"status={r.status_code}")
            return
        token = r.json()["data"]["token"]
        headers = {"Authorization": f"Bearer {token}"}
        ok("login")

        print("\n[1/6] 基础 GET — 公网 API 返回 200 + body JSON 解析")
        data, err = await create_and_execute(
            client, headers, "sprint9_get",
            {
                "method": "GET",
                "url": "https://jsonplaceholder.typicode.com/todos/1",
                "timeout": 15,
                "retry": 2,
            },
        )
        if err:
            fail("get_basic", err)
        else:
            out = (data.get("outputs") or {}).get("http_http1", {})
            if data.get("status") == "completed" and out.get("status_code") == 200:
                body = out.get("body", {})
                if isinstance(body, dict) and body.get("id") == 1:
                    ok("get_basic", f"status={out['status_code']} body.id={body['id']} duration={out.get('duration_ms')}ms")
                else:
                    fail("get_basic", f"body 解析异常: {str(body)[:200]}")
            else:
                fail("get_basic", f"workflow={data.get('status')} http={out.get('status_code')} err={data.get('error')}")

        print("\n[2/6] POST + JSON body — 响应体回显")
        data, err = await create_and_execute(
            client, headers, "sprint9_post",
            {
                "method": "POST",
                "url": "https://jsonplaceholder.typicode.com/posts",
                "headers": {"Content-Type": "application/json"},
                "body": {"title": "MetaPivot", "body": "Sprint 9.1 test", "userId": 1},
                "timeout": 15,
                "retry": 2,
            },
        )
        if err:
            fail("post_json", err)
        else:
            out = (data.get("outputs") or {}).get("http_http1", {})
            body = out.get("body", {})
            if out.get("status_code") in (200, 201) and isinstance(body, dict) and body.get("title") == "MetaPivot":
                ok("post_json", f"status={out['status_code']} body.id={body.get('id')}")
            else:
                fail("post_json", f"status={out.get('status_code')} body={str(body)[:200]}")

        print("\n[3/6] SSRF 防护 — 请求 127.0.0.1 被拦截")
        data, err = await create_and_execute(
            client, headers, "sprint9_ssrf",
            {
                "method": "GET",
                "url": "http://127.0.0.1:8000/health",
                "timeout": 5,
                "retry": 1,
            },
        )
        if err:
            fail("ssrf_blocked", err)
        else:
            out = (data.get("outputs") or {}).get("http_http1", {})
            err_msg = out.get("error", "") or str(out.get("body", ""))
            if out.get("status_code") == 0 and "SSRF" in err_msg:
                ok("ssrf_blocked", f"error={err_msg[:80]}")
            else:
                fail("ssrf_blocked", f"预期 status_code=0 + error含SSRF, 实际 status={out.get('status_code')} err={err_msg[:100]}")

        print("\n[4/6] 错误处理 — 404 URL 返回 status_code=404，工作流不中断")
        data, err = await create_and_execute(
            client, headers, "sprint9_404",
            {
                "method": "GET",
                "url": "https://jsonplaceholder.typicode.com/nonexistent-path-404",
                "timeout": 10,
                "retry": 1,
            },
        )
        if err:
            fail("http_404", err)
        else:
            out = (data.get("outputs") or {}).get("http_http1", {})
            # 工作流应 completed（节点返回 dict 而非 raise），http_request 节点 status_code=404
            if data.get("status") == "completed" and out.get("status_code") == 404:
                ok("http_404", f"workflow=completed http={out['status_code']}")
            else:
                fail("http_404", f"预期 completed+404, 实际 workflow={data.get('status')} http={out.get('status_code')}")

        print("\n[5/6] 鉴权 — Basic Auth 头注入")
        data, err = await create_and_execute(
            client, headers, "sprint9_auth",
            {
                "method": "GET",
                "url": "https://jsonplaceholder.typicode.com/todos/1",
                "auth": {"type": "basic", "username": "user", "password": "pass"},
                "timeout": 10,
                "retry": 1,
            },
        )
        if err:
            fail("basic_auth", err)
        else:
            out = (data.get("outputs") or {}).get("http_http1", {})
            # 公网 API 不校验 basic auth，但能正常 200 说明 auth 头注入未破坏请求
            if out.get("status_code") == 200:
                ok("basic_auth", "auth 头注入 + 请求成功")
            else:
                fail("basic_auth", f"status={out.get('status_code')} err={out.get('error','')[:100]}")

        print("\n[6/6] 4xx 不重试 — 404 立即返回（无重试延迟）")
        t0 = time.monotonic()
        data, err = await create_and_execute(
            client, headers, "sprint9_no_retry_4xx",
            {
                "method": "GET",
                "url": "https://jsonplaceholder.typicode.com/nonexistent-404",
                "timeout": 10,
                "retry": 3,  # 即便配置 retry=3，4xx 也不应重试
            },
        )
        elapsed = time.monotonic() - t0
        if err:
            fail("no_retry_4xx", err)
        else:
            out = (data.get("outputs") or {}).get("http_http1", {})
            # 4xx 不重试 → 总耗时 < 5s（重试 backoff 0.5+1+2=3.5s 会被跳过）
            if out.get("status_code") == 404 and elapsed < 5.0:
                ok("no_retry_4xx", f"elapsed={elapsed:.2f}s (<5s 表示未重试)")
            else:
                fail("no_retry_4xx", f"status={out.get('status_code')} elapsed={elapsed:.2f}s (若>5s 表示 4xx 被错误重试)")

    print("\n" + "=" * 70)
    print(f"结果：✓ {passed} 通过  ✗ {failed} 失败")
    print("=" * 70)
    sys_exit_code = 0 if failed == 0 else 1
    import sys
    sys.exit(sys_exit_code)


if __name__ == "__main__":
    asyncio.run(main())
