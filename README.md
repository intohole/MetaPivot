# MetaPivot

> 企业内部多 IM 渠道自动化办公服务

提供钉钉/企微/飞书三端 IM 接入、智能问答自动回复、可视化工作流编排、超级 Agent 自主完成任务，基于 MCP / Function Call / Skill 三层能力体系，帮助企业员工提效。

## 核心能力

| 能力 | 说明 |
|------|------|
| **多渠道 IM 接入** | 钉钉 Stream / 飞书长连接 / 企微 Webhook，统一 ChannelAdapter 抽象 |
| **超级 Agent** | 自定义状态机（INTENT→PLANNING→EXECUTING→HITL→REFLECTING→REPLY），自主规划 + 工具调用 |
| **可视化工作流** | DAG 引擎 + 状态机，支持 skill_call / llm_call / condition / send_message / hitl 节点 |
| **三层能力体系** | Skill（业务级）→ MCP Server（协议级）→ Function Call（原子工具），热插拔 |
| **HITL 人工确认** | 敏感操作暂停等待确认，IM 卡片回调恢复执行 |
| **审计与回滚** | 全量审计日志，输入哈希 + 输出脱敏，留存 6 个月+ |

## 架构分层

```
Route层 → Service层 → Domain(领域)/Infra(基础设施) → Data(持久化) → Utils(工具)
```
依赖方向严格向下，详见 [docs/architecture.md](docs/architecture.md)。

## 技术栈

- **Web**：FastAPI + Uvicorn（异步原生）
- **Agent**：自定义状态机（替代 LangGraph，避免 Pydantic 兼容性问题）
- **LLM**：OpenAI 兼容 SDK（支持 Kimi/Qwen/GLM）
- **MCP**：mcp + FastMCP
- **DB**：PostgreSQL（元数据/审计/工作流）+ Redis（会话/限流/PubSub）+ Milvus（向量库）
- **IM SDK**：dingtalk-stream / lark-oapi / pycryptodome
- **日志**：loguru（文件轮转，保留 3 天）

## 快速开始

### 1. 环境准备

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # 按需修改配置
```

### 2. 启动依赖（Docker）

```bash
docker-compose up -d postgres redis milvus
```

### 3. 初始化数据库

```bash
python -m app.infra.db.init_db
```

### 4. 启动服务

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

访问 http://localhost:8000/docs 查看 API 文档。

### 5. 冒烟测试

```bash
python scripts/smoke_test.py
```

## 项目结构

```
MetaPivot/
├── app/
│   ├── main.py              # FastAPI 入口
│   ├── route/               # 路由层（8 个路由模块）
│   ├── service/             # Service 层（agent/skill/workflow/channel/audit/auth/...）
│   ├── domain/              # Domain 层（agent 状态机 / workflow DAG / channel 适配器抽象）
│   ├── infra/               # Infra 层（db / cache / llm / mcp / channel / tools / rag）
│   └── utils/               # Utils 层（config / logger / response / security）
├── docs/
│   ├── architecture.md      # 架构设计
│   ├── data-model.md        # 数据模型
│   └── quickstart.md        # 快速开始
├── scripts/
│   └── smoke_test.py        # 冒烟测试
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── llms.txt                 # API 规范（Agent 可读）
```

## API 概览

| 模块 | 前缀 | 说明 |
|------|------|------|
| 认证 | `/api/v1/auth` | 登录 / 刷新 / 当前用户 |
| IM 接入 | `/api/v1/im` | 三端 webhook + 状态查询 |
| Agent | `/api/v1/agent` | 对话 / 任务查询 / SSE 流 / HITL 确认 |
| Skill | `/api/v1/skills` | CRUD / 启停 / 测试 |
| 工作流 | `/api/v1/workflows` | CRUD / 执行 / 执行记录 |
| 知识库 | `/api/v1/knowledge` | 文档上传 / 检索 |
| 审计 | `/api/v1/audit` | 日志查询 / 统计 |
| 管理 | `/api/v1` | 用户 / 角色 / 配置 |

完整 API 规范见 [llms.txt](llms.txt)，运行后访问 `/docs` 查看 OpenAPI。

## 部署

```bash
docker-compose up -d
```

包含 postgres / redis / milvus / api / celery worker。

## 安全说明

- 全栈私有化部署，数据不出内网
- JWT 认证 + RBAC（user/manager/admin 三级）
- 敏感操作 HITL 确认
- 全量审计日志，留存 6 个月+
- 等保 2.0 三级合规建议

## 开发约定

- 文件行数 ≤ 300，类内高度内聚
- 所有 IO 异步，禁止阻塞主线程
- 全局 loguru 日志，保留 3 天
- 分层依赖方向严格向下

## License

Internal use only.
