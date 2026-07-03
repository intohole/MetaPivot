# MetaPivot

> 企业内部多 IM 渠道自动化办公服务

提供钉钉/企微/飞书三端 IM 接入、智能问答自动回复、可视化工作流编排、超级 Agent 自主完成任务，基于 MCP / Function Call / Skill 三层能力体系，帮助企业员工提效。

## 🚀 30 秒快速开始（小企业零依赖部署）

```bash
# 1. 安装依赖
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. 配置（小企业默认：SQLite + 内存缓存 + 本地向量，无需 PostgreSQL/Redis/Milvus）
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY（必填，支持 Kimi/Qwen/GLM/DeepSeek）

# 3. 启动（自动建表 + 初始化管理员）
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

访问 http://localhost:8000 → 管理后台（admin / admin123，请立即改密）
API 文档：http://localhost:8000/docs

## 部署规模（资源可伸缩）

所有基础设施均已接口化，通过 `.env` 一行配置切换 backend，适配小企业到超大型企业：

| 部署规模 | DB_BACKEND | CACHE_BACKEND | VECTOR_BACKEND | 外部依赖 | 适用场景 |
|---------|-----------|--------------|----------------|---------|---------|
| **小企业** | `sqlite` | `memory` | `local` | 零（仅 Python） | < 50 人团队，单机部署 |
| **中型** | `postgresql` | `redis` | `local` | PostgreSQL + Redis | 多实例，共享会话 |
| **超大型** | `postgresql` | `redis` | `milvus` | PostgreSQL + Redis + Milvus | 百万级知识库，高并发 |

```bash
# .env 配置示例（小企业 → 中型，只需改 3 行）
DB_BACKEND=postgresql      # sqlite → postgresql
CACHE_BACKEND=redis        # memory → redis
VECTOR_BACKEND=milvus      # local → milvus（可选）
```

## 核心能力

| 能力 | 说明 |
|------|------|
| **多渠道 IM 接入** | 钉钉 Stream / 飞书长连接 / 企微 Webhook，统一 ChannelAdapter 抽象 |
| **超级 Agent** | LLM 意图分类 + 并行工具调用 + 流式回复 + HITL 人工确认 + stuck 检测 |
| **可视化工作流** | DAG 引擎 + 状态机，支持 skill_call / llm_call / condition / send_message / hitl 节点 |
| **三层能力体系** | Skill（业务级）→ MCP Server（协议级）→ Function Call（原子工具），热插拔 |
| **HITL 人工确认** | 敏感操作暂停等待确认，IM 卡片回调恢复执行 |
| **接口化架构** | IDatabase/ICache/IVectorStore/ILLMProvider Protocol，用户可自定义实现替换 |
| **审计与回滚** | 全量审计日志，输入哈希 + 输出脱敏，留存 6 个月+ |

## 架构分层

```
Route层 → Service层 → Domain(领域)/Infra(基础设施) → Data(持久化) → Utils(工具)
```

依赖方向严格向下，详见 [docs/architecture.md](docs/architecture.md)。

### 接口化设计（Protocol Contracts）

```
app/domain/contracts/          # Domain 层声明接口（Protocol）
├── cache.py                  # ICache: get/set/delete/acquire_lock/rate_limit
├── vector.py                 # IVectorStore: upsert/search/delete/count
└── llm.py                    # ILLMProvider: chat_completion/chat_stream/embed

app/infra/                    # Infra 层提供实现，工厂模式按配置切换
├── cache/{memory,redis_cache,factory}.py
├── rag/{local_vector,factory}.py
└── db/session.py             # SQLite/PostgreSQL 引擎工厂
```

新增 backend 只需实现 Protocol 接口，无需改动业务代码。

## 技术栈

- **Web**：FastAPI + Uvicorn（异步原生）
- **Agent**：自定义状态机（LLM 意图分类 + 并行工具 + 流式回复）
- **LLM**：OpenAI 兼容 SDK（支持 Kimi/Qwen/GLM/DeepSeek）
- **DB**：SQLAlchemy 2.0 async（SQLite / PostgreSQL 可切换）
- **缓存**：MemoryCache / Redis 可切换（ICache Protocol）
- **向量库**：LocalVectorStore / Milvus 可切换（IVectorStore Protocol）
- **MCP**：mcp + FastMCP
- **IM SDK**：dingtalk-stream / lark-oapi / pycryptodome（可选）
- **日志**：loguru（文件轮转，保留 3 天）

## 快速开始（详细）

### 1. 环境准备

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### 2. 配置 LLM（必填）

编辑 `.env`，填入大模型 API Key：

```bash
LLM_PROVIDER=kimi              # kimi / qwen / glm / deepseek
LLM_API_KEY=sk-your-real-key   # 必填
LLM_MODEL=kimi-k2-6            # 按需选择模型
```

### 3. 启动服务

```bash
# 小企业（零外部依赖，默认配置）
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 中型/大型企业（需先启动 PostgreSQL/Redis）
docker-compose up -d postgres redis  # 可选 milvus
# 然后修改 .env：DB_BACKEND=postgresql, CACHE_BACKEND=redis
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

首次启动自动建表 + 创建管理员（admin / admin123）。

### 4. 验证

```bash
# 健康检查（含 backend 信息）
curl http://localhost:8000/health

# 就绪检查
curl http://localhost:8000/ready

# 登录获取 Token
curl -X POST http://localhost:8000/api/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'
```

## 项目结构

```
MetaPivot/
├── app/
│   ├── main.py              # FastAPI 入口
│   ├── route/               # 路由层（8 个路由模块）
│   ├── service/             # Service 层（agent/skill/workflow/channel/audit/auth/...）
│   ├── domain/              # Domain 层
│   │   ├── agent/           # Agent 状态机（graph/nodes/intent/prompts/state/guardrail）
│   │   ├── contracts/       # Protocol 契约（ICache/IVectorStore/ILLMProvider）
│   │   ├── workflow/        # 工作流 DAG 引擎
│   │   └── channel/         # IM 渠道适配器抽象
│   ├── infra/               # Infra 层（db/cache/llm/mcp/channel/tools/rag）
│   └── utils/               # Utils 层（config/logger/response/security）
├── static/                  # 管理后台前端（Vue 3 CDN，零编译）
│   ├── index.html
│   ├── css/styles.css
│   └── js/{store,api,components,app}.js + pages/
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

## 管理后台

访问 http://localhost:8000 即可视化管理：
- **Dashboard**：统计概览 + 快捷操作
- **Agent 对话**：实时对话 + 任务历史 + SSE 流式
- **Skill 管理**：CRUD / 启停 / 测试
- **工作流**：DAG 编排 / 执行记录
- **知识库**：文档上传 / 向量检索
- **审计日志**：全量操作追溯
- **用户/IM渠道/系统配置**：可视化管理

## 部署（Docker）

```bash
# 小企业（单容器）
docker build -t metapivot .
docker run -p 8000:8000 -v $(pwd)/data:/app/data metapivot

# 大型企业（完整栈）
docker-compose up -d
```

## 安全说明

- 全栈私有化部署，数据不出内网
- JWT 认证 + RBAC（user/manager/admin 三级）
- Guardrail 安全护栏（PII 脱敏 + prompt injection 检测）
- 敏感操作 HITL 确认
- 全量审计日志，留存 6 个月+
- 等保 2.0 三级合规建议

## 开发约定

- 文件行数 ≤ 300，类内高度内聚
- 所有 IO 异步，禁止阻塞主线程
- 全局 loguru 日志，保留 3 天
- 分层依赖方向严格向下
- 接口优先：Protocol 契约 + 工厂模式

## License

Internal use only.
