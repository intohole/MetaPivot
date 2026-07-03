"""冒烟测试 - 验证应用骨架可启动，路由可响应

策略：mock 掉 DB/Redis/Channel 初始化，用 TestClient 测试基础端点
真实 DB/Redis 连接由 docker-compose 启动后验证

运行：
    .venv/bin/python scripts/smoke_test.py
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

# 注入项目根路径，确保可直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    """启动应用并测试基础端点"""
    # Mock 外部依赖初始化
    # 注意：start_channels/stop_channels/register_to_channel_service 在 lifespan 内部导入，
    # 必须在原始模块路径上 patch，而不是 app.main
    patches = [
        patch("app.main.init_db", new=AsyncMock(return_value=None)),
        patch("app.main.init_redis", new=AsyncMock(return_value=None)),
        patch("app.main.close_db", new=AsyncMock(return_value=None)),
        patch("app.main.close_redis", new=AsyncMock(return_value=None)),
        patch(
            "app.service.message_handler.register_to_channel_service",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.service.channel_manager.start_channels",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.service.channel_manager.stop_channels",
            new=AsyncMock(return_value=None),
        ),
    ]
    for p in patches:
        p.start()
    try:
        from fastapi.testclient import TestClient
        from app.main import app

        with TestClient(app) as client:
            print("=" * 60)
            print("MetaPivot 冒烟测试")
            print("=" * 60)

            # 1. 健康检查
            r = client.get("/health")
            print(f"[GET /health] {r.status_code}")
            assert r.status_code == 200, f"/health 失败: {r.text}"
            data = r.json()
            print(f"  status={data.get('status')}, version={data.get('version')}")
            print("  ✓ 健康检查通过")

            # 2. OpenAPI 文档可访问
            r = client.get("/openapi.json")
            print(f"[GET /openapi.json] {r.status_code}")
            assert r.status_code == 200, f"/openapi.json 失败: {r.text}"
            paths = r.json().get("paths", {})
            print(f"  已注册端点数: {len(paths)}")
            print("  ✓ OpenAPI 文档可访问")

            # 3. 认证失败（无 token）
            r = client.get("/api/v1/auth/me")
            print(f"[GET /api/v1/auth/me (无token)] {r.status_code}")
            assert r.status_code in (401, 403), f"认证校验失败: {r.text}"
            print("  ✓ JWT 认证拦截生效")

            # 4. IM 状态查询（无需认证）
            r = client.get("/api/v1/im/status")
            print(f"[GET /api/v1/im/status] {r.status_code}")
            assert r.status_code == 200, f"/im/status 失败: {r.text}"
            print(f"  channels={r.json().get('data', {}).get('channels', [])}")
            print("  ✓ IM 状态查询通过")

            # 5. 错误响应格式校验
            r = client.get("/api/v1/agent/tasks/nonexistent")
            print(f"[GET /api/v1/agent/tasks/nonexistent] {r.status_code}")
            assert r.status_code in (401, 404), f"错误响应格式异常: {r.text}"
            print("  ✓ 错误响应格式正确")

            print("=" * 60)
            print("✅ 冒烟测试全部通过")
            print("=" * 60)
            print(f"\n应用共注册 {len(app.routes)} 个路由")
            print("启动验证完成，生产环境请用 docker-compose 启动完整依赖")
    finally:
        for p in patches:
            p.stop()


if __name__ == "__main__":
    main()
