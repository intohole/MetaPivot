"""FastAPI 应用入口 - MetaPivot"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.infra.cache.redis_client import close_redis, init_redis
from app.infra.db.init_db import init_db
from app.infra.db.session import check_db_health, close_db
from app.utils.config import settings
from app.utils.logger import get_logger, setup_logger
from app.utils.response import AppError, error_response

# 模块级日志器（setup_logger 在 lifespan 中调用，此处仅获取实例）
log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化，关闭时清理"""
    setup_logger()
    log.info("Starting {} v{} ...", settings.app_name, settings.app_version)

    # Phase 4: 初始化 OTel + Langfuse 链路追踪（otel_enabled=False 时使用 NoopTracer）
    from app.infra.observability.otel import init_otel
    init_otel()

    # 启动前配置校验（不阻断，仅 WARNING 日志）
    from app.utils.config_validator import validate_config
    await validate_config()

    # 初始化数据库
    await init_db()

    # 任务恢复：将上次崩溃时未完成的任务标记为 failed/cancelled
    from app.service.task_recovery import recover_stuck_tasks
    recovered = await recover_stuck_tasks()
    if recovered:
        log.info("Recovered {} stuck tasks from previous run", recovered)

    # 初始化Redis
    await init_redis()
    # 初始化事件总线（Local 单机 / Redis 集群，跟随 CACHE_BACKEND）
    # 通过 DI 注入到 StreamManager，避免 Domain 层直接依赖 Infra
    from app.infra.event.factory import init_event_bus
    from app.domain.agent.stream import stream_manager
    bus = await init_event_bus()
    stream_manager.set_bus(bus)
    # 初始化多轮对话记忆存储（DI 注入到 AgentService）
    # Local: InMemoryMemoryStore（开发环境）/ DB: DBMemoryStore（生产，跟随 DB_BACKEND）
    from app.infra.memory.factory import get_memory_store
    from app.service.agent_service import agent_service
    memory_store = await get_memory_store()
    agent_service.set_memory_store(memory_store)
    # 初始化定时任务调度器（DI 启动后台轮询；AsyncScheduler 单机 / CeleryScheduler 集群）
    from app.infra.scheduler.factory import get_scheduler
    scheduler = await get_scheduler()
    await scheduler.start()
    # 注册 IM 消息处理器（桥接 ChannelService ↔ AgentService）
    from app.service.message_handler import register_to_channel_service
    await register_to_channel_service()

    # 启动IM渠道（异步任务）
    from app.service.channel_manager import start_channels
    await start_channels()

    log.info("{} started on port {}", settings.app_name, settings.app_port)
    yield

    # 优雅关闭：取消在途任务 → 停 IM → 释放 scheduler/event_bus/memory/DB/cache/otel
    log.info("Shutting down {}...", settings.app_name)
    from app.service.shutdown import graceful_shutdown
    await graceful_shutdown()
    from app.infra.scheduler.factory import close_scheduler
    await close_scheduler()
    from app.infra.event.factory import close_event_bus
    await close_event_bus()
    from app.infra.memory.factory import close_memory_store
    await close_memory_store()
    # Phase A 补丁：关闭向量库连接（Milvus/Chroma client 资源释放，避免 K8s 滚动更新连接泄漏）
    from app.infra.rag.factory import close_vector_store
    await close_vector_store()
    await close_db()
    await close_redis()
    # Phase 4: 关闭 OTel SDK，flush 待上报 span
    from app.infra.observability.otel import shutdown_otel
    shutdown_otel()
    log.info("{} stopped", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    description="企业内部多IM渠道自动化办公服务",
    version=settings.app_version,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    lifespan=lifespan,
)

# CORS中间件（生产环境需配置具体域名）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.app_debug else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 限流中间件（Redis 令牌桶，按 IP + 路径维度）
from app.middleware.rate_limit import RateLimitMiddleware  # noqa: E402
app.add_middleware(RateLimitMiddleware)

# 指标中间件（HTTP 请求计数/延迟 → Prometheus）
from app.middleware.metrics_middleware import MetricsMiddleware  # noqa: E402
app.add_middleware(MetricsMiddleware)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    """请求中间件：注入request_id + 错误处理 + 日志

    通过 contextvars 写入 request_id，使后续 asyncio.create_task 创建的后台任务
    也能在日志中带上 request_id（loguru patcher 从 contextvars 读取）。
    """
    import uuid as uuid_mod
    from app.utils.context import set_request_context, clear_request_context

    request_id = request.headers.get("X-Request-ID", str(uuid_mod.uuid4()))
    trace_id = request.headers.get("X-Trace-ID", request_id)
    client_ip = request.client.host if request.client else ""
    request.state.request_id = request_id
    # 写入 contextvars，后台任务通过 copy_context 自动继承
    set_request_context(request_id, trace_id=trace_id, ip_address=client_ip)

    from loguru import logger as _logger
    log_ctx = _logger.bind(request_id=request_id)
    log_ctx.info("{} {}", request.method, request.url.path)

    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if trace_id != request_id:
            response.headers["X-Trace-ID"] = trace_id
        return response
    except AppError as e:
        log_ctx.error("AppError: {} - {}", e.code, e.message)
        return JSONResponse(
            status_code=e.status_code,
            content=error_response(e.code, e.message, request_id, e.details),
        )
    except Exception as e:
        log_ctx.exception("Unhandled exception: {}", e)
        return JSONResponse(
            status_code=500,
            content=error_response("INTERNAL_ERROR", str(e), request_id),
        )
    finally:
        clear_request_context()


# 健康检查端点
@app.get("/health", tags=["health"])
async def health():
    """健康检查"""
    return {
        "status": "healthy",
        "version": settings.app_version,
        "env": settings.app_env,
        "profile": {
            "db": settings.db_backend,
            "cache": settings.cache_backend,
            "vector": settings.vector_backend,
        },
    }


@app.get("/ready", tags=["health"])
async def readiness():
    """就绪检查（按配置的 backend 检查实际依赖）"""
    db_ok = await check_db_health()
    from app.infra.cache.factory import check_cache_health
    cache_ok = await check_cache_health()
    from app.infra.event.factory import check_event_bus_health
    bus_ok = await check_event_bus_health()
    from app.infra.memory.factory import check_memory_health
    memory_ok = await check_memory_health()
    from app.infra.scheduler.factory import check_scheduler_health
    scheduler_ok = await check_scheduler_health()
    from app.infra.rag.factory import check_vector_health
    vector_ok = await check_vector_health()
    ready = db_ok and cache_ok and bus_ok and memory_ok and scheduler_ok and vector_ok
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "dependencies": {
                "db": {"backend": settings.db_backend, "ok": db_ok},
                "cache": {"backend": settings.cache_backend, "ok": cache_ok},
                "vector": {"backend": settings.vector_backend, "ok": vector_ok},
                "event_bus": {"backend": settings.cache_backend, "ok": bus_ok},
                "memory": {"backend": settings.memory_backend, "ok": memory_ok},
                "scheduler": {"backend": settings.scheduler_backend, "ok": scheduler_ok},
            },
        },
    )


@app.get("/metrics", tags=["metrics"], include_in_schema=False)
async def metrics():
    """Prometheus 指标端点（供 Prometheus 抓取）"""
    from app.utils.metrics import render_metrics
    from fastapi.responses import Response
    return Response(content=render_metrics(), media_type="text/plain")


# 注册路由
from app.route import im_routes, agent_routes, skill_routes, workflow_routes  # noqa: E402
from app.route import audit_routes, admin_routes, auth_routes, knowledge_routes  # noqa: E402
from app.route import schedule_routes, replay_routes, dlq_routes  # noqa: E402

app.include_router(auth_routes.router, prefix="/api/v1/auth", tags=["认证"])
app.include_router(im_routes.router, prefix="/api/v1/im", tags=["IM接入"])
app.include_router(agent_routes.router, prefix="/api/v1/agent", tags=["Agent"])
# Phase 4: 会话重放（GET /tasks/{id}/replay 返回事件流 + Langfuse URL）
app.include_router(replay_routes.router, prefix="/api/v1/agent", tags=["Agent"])
app.include_router(skill_routes.router, prefix="/api/v1/skills", tags=["Skill管理"])
app.include_router(workflow_routes.router, prefix="/api/v1/workflows", tags=["工作流"])
app.include_router(knowledge_routes.router, prefix="/api/v1/knowledge", tags=["知识库"])
app.include_router(audit_routes.router, prefix="/api/v1/audit", tags=["审计"])
# Phase 5: DLQ 死信队列（必须在 schedule_routes 之前注册，避免 /schedules/{task_id} 拦截 /schedules/dlq）
app.include_router(dlq_routes.router, prefix="/api/v1/schedules/dlq", tags=["定时任务DLQ"])
app.include_router(schedule_routes.router, prefix="/api/v1/schedules", tags=["定时任务"])
app.include_router(admin_routes.router, prefix="/api/v1", tags=["管理"])

# 挂载静态文件服务（管理后台前端）
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
import os as _os  # noqa: E402
_static_dir = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "static")
if _os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")
    log.info("Static files mounted at /static")

    # 根路由返回管理后台首页（hash 路由的 SPA，仅 / 需后端返回 HTML）
    @app.get("/", include_in_schema=False, tags=["frontend"])
    async def serve_admin_index():
        """根路由：返回管理后台首页"""
        _index = _os.path.join(_static_dir, "index.html")
        if _os.path.exists(_index):
            return FileResponse(_index)
        return JSONResponse({"message": "MetaPivot API", "docs": "/docs"})


# 注：生产部署若通过 `uvicorn app.main:app` 启动（非 python -m app.main），
# 请追加 CLI 参数 `--timeout-graceful-shutdown 25` 以匹配 K8s
# terminationGracePeriodSeconds=30 的优雅关闭窗口。
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_debug,
        log_level=settings.app_log_level.lower(),
        # K8s 兼容：terminationGracePeriodSeconds 默认 30s，留 5s 余量
        timeout_graceful_shutdown=25,
    )
