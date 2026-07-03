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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化，关闭时清理"""
    setup_logger()
    log = get_logger("main")
    log.info("Starting {} v{} ...", settings.app_name, settings.app_version)

    # 初始化数据库
    await init_db()
    # 初始化Redis
    await init_redis()
    # 注册 IM 消息处理器（桥接 ChannelService ↔ AgentService）
    from app.service.message_handler import register_to_channel_service
    await register_to_channel_service()

    # 启动IM渠道（异步任务）
    from app.service.channel_manager import start_channels
    await start_channels()

    log.info("{} started on port {}", settings.app_name, settings.app_port)
    yield

    # 关闭
    log.info("Shutting down {}...", settings.app_name)
    from app.service.channel_manager import stop_channels
    await stop_channels()
    await close_db()
    await close_redis()
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


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    """请求中间件：注入request_id + 错误处理 + 日志"""
    import uuid as uuid_mod
    request_id = request.headers.get("X-Request-ID", str(uuid_mod.uuid4()))
    request.state.request_id = request_id

    from loguru import logger as _logger
    log_ctx = _logger.bind(request_id=request_id)
    log_ctx.info("{} {}", request.method, request.url.path)

    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
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


# 健康检查端点
@app.get("/health", tags=["health"])
async def health():
    """健康检查"""
    return {
        "status": "healthy",
        "version": settings.app_version,
        "env": settings.app_env,
    }


@app.get("/ready", tags=["health"])
async def readiness():
    """就绪检查"""
    db_ok = await check_db_health()
    from app.infra.cache.redis_client import check_redis_health
    redis_ok = await check_redis_health()
    ready = db_ok and redis_ok
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "dependencies": {"postgres": db_ok, "redis": redis_ok},
        },
    )


# 注册路由
from app.route import im_routes, agent_routes, skill_routes, workflow_routes  # noqa: E402
from app.route import audit_routes, admin_routes, auth_routes, knowledge_routes  # noqa: E402

app.include_router(auth_routes.router, prefix="/api/v1/auth", tags=["认证"])
app.include_router(im_routes.router, prefix="/api/v1/im", tags=["IM接入"])
app.include_router(agent_routes.router, prefix="/api/v1/agent", tags=["Agent"])
app.include_router(skill_routes.router, prefix="/api/v1/skills", tags=["Skill管理"])
app.include_router(workflow_routes.router, prefix="/api/v1/workflows", tags=["工作流"])
app.include_router(knowledge_routes.router, prefix="/api/v1/knowledge", tags=["知识库"])
app.include_router(audit_routes.router, prefix="/api/v1/audit", tags=["审计"])
app.include_router(admin_routes.router, prefix="/api/v1", tags=["管理"])


@app.on_event("shutdown")
async def shutdown_event():
    """关闭事件（兼容旧版）"""
    pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_debug,
        log_level=settings.app_log_level.lower(),
    )
