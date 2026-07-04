# MetaPivot 快速接入指南

## 前置准备

1. Docker 24+ 和 Docker Compose 已安装
2. 申请IM开放平台应用凭证（按需）：
   - 钉钉：企业内部应用 → ClientID + ClientSecret
   - 企业微信：自建应用 → CorpID + Secret + Token + EncodingAESKey
   - 飞书：自建应用 → AppID + AppSecret
3. LLM API Key（任选其一）：Kimi / 通义千问 / GLM / DeepSeek

## 一、启动服务

```bash
# 1. 克隆项目
git clone <repo_url> metapivot
cd metapivot

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入凭证（参考下方配置说明）

# 3. 一键启动（PostgreSQL + Redis + Milvus + 后端服务）
docker-compose up -d

# 4. 初始化数据库
docker-compose exec api python -m app.infra.db.init_db

# 5. 健康检查
curl http://localhost:8000/health
# 预期: {"status":"healthy","version":"1.0.0",...}
```

## 二、环境变量配置（.env）

```bash
# ============ LLM 配置 ============
LLM_PROVIDER=kimi                    # kimi/qwen/glm/deepseek
LLM_API_KEY=sk-xxxxxxxxxxxxxxxx
LLM_MODEL=kimi-k2-6                  # 或 qwen3.6-plus / glm-5.2

# ============ 数据库 ============
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=metapivot
POSTGRES_USER=metapivot
POSTGRES_PASSWORD=<strong_password>

# ============ Redis ============
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=<strong_password>

# ============ Milvus ============
MILVUS_HOST=milvus
MILVUS_PORT=19530

# ============ 钉钉（可选）============
DINGTALK_CLIENT_ID=dingxxxxxxxx
DINGTALK_CLIENT_SECRET=xxxxxxxx

# ============ 企业微信（可选）============
WECOM_CORP_ID=wwxxxxxxxx
WECOM_APP_SECRET=xxxxxxxx
WECOM_TOKEN=xxxxxxxx
WECOM_ENCODING_AES_KEY=xxxxxxxx

# ============ 飞书（可选）============
FEISHU_APP_ID=cli_xxxxxxxx
FEISHU_APP_SECRET=xxxxxxxx

# ============ 安全 ============
JWT_SECRET=<random_64_chars>
JWT_EXPIRES_IN=3600
ENCRYPT_KEY=<random_32_chars>
```

## 三、首次调用

### 1. 获取JWT令牌

```bash
curl -X POST http://localhost:8000/api/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'
```

响应：
```json
{
  "success": true,
  "data": {
    "token": "eyJhbGciOiJIUzI1NiIs...",
    "token_type": "Bearer",
    "expires_in": 3600,
    "user": {"id":"u_admin","username":"admin","role":"admin"}
  },
  "request_id": "req_xxx"
}
```

### 2. 与超级Agent对话

```bash
TOKEN="eyJhbGciOiJIUzI1NiIs..."

curl -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"帮我查一下明天北京的天气并设置日历提醒","channel":"api"}'
```

响应（异步任务）：
```json
{
  "success": true,
  "data": {
    "task_id": "task_xxx",
    "status": "pending",
    "stream_url": "/api/v1/agent/tasks/task_xxx/stream"
  }
}
```

### 3. 订阅任务流式结果（SSE）

```bash
curl -N -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/agent/tasks/task_xxx/stream
```

SSE 事件流：
```
event: step_started
data: {"step":1,"name":"查询天气","tool":"weather_query"}

event: tool_call
data: {"tool":"weather_query","input":{"city":"北京","date":"2026-07-05"},"output":{"temp":28,"weather":"晴"}}

event: human_confirm_required
data: {"step":2,"message":"将为明天北京天气设置日历提醒，是否确认？","options":["approve","reject"]}

event: final_result
data: {"result":"已为您查询到明天北京天气（28℃晴），并设置了日历提醒。"}
```

### 4. 人工确认（敏感操作）

```bash
curl -X POST http://localhost:8000/api/v1/agent/tasks/task_xxx/confirm \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"decision":"approve"}'
```

## 四、注册第一个Skill

```bash
# 创建一个调用MCP的Skill
curl -X POST http://localhost:8000/api/v1/skills \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "leave_apply",
    "description": "提交员工请假申请。当用户需要请假时调用此Skill，会创建审批流程。",
    "input_schema": {
      "type": "object",
      "properties": {
        "leave_type": {"type":"string","enum":["annual","sick","personal"],"description":"请假类型"},
        "start_date": {"type":"string","description":"开始日期 YYYY-MM-DD"},
        "end_date": {"type":"string","description":"结束日期 YYYY-MM-DD"},
        "reason": {"type":"string","description":"请假事由"}
      },
      "required": ["leave_type","start_date","end_date","reason"]
    },
    "source_type": "mcp",
    "source_ref": "hr-system/apply_leave",
    "permission": "user",
    "require_confirm": true,
    "tags": ["hr","leave"]
  }'
```

## 五、对接IM渠道

### 钉钉（Stream模式，推荐）

1. 在钉钉开放平台创建企业内部应用
2. 开启机器人能力，配置消息接收为Stream模式
3. 在`.env`填入`DINGTALK_CLIENT_ID`和`DINGTALK_CLIENT_SECRET`
4. 重启服务，自动建立WebSocket长连接
5. 在钉钉群内@机器人发送消息即可触发Agent

### 飞书（长连接模式，推荐）

1. 在飞书开放平台创建自建应用
2. 添加机器人能力，配置事件订阅为"使用长连接接收回调"
3. 在`.env`填入`FEISHU_APP_ID`和`FEISHU_APP_SECRET`
4. 重启服务，自动建立长连接
5. 在飞书内@机器人发送消息即可触发Agent

### 企业微信（Webhook模式）

1. 在企业微信管理后台创建自建应用
2. 配置"接收消息"API，回调URL填入：`https://<your_domain>/api/v1/im/wecom/callback`
3. 在`.env`填入`WECOM_CORP_ID`、`WECOM_APP_SECRET`、`WECOM_TOKEN`、`WECOM_ENCODING_AES_KEY`
4. 重启服务，企微会发送验证请求，服务自动响应echostr
5. 在企微内@应用机器人发送消息即可触发Agent

## 六、常见问题

| 问题 | 解决方案 |
|------|----------|
| IM消息无响应 | 检查`/health`端点；查看日志`docker-compose logs api` |
| Agent超时 | 调整`LLM_TIMEOUT`和`MAX_STEPS`配置 |
| 钉钉频次超限 | 购买钉钉专业版或企业开发增购包 |
| LLM调用失败 | 检查API Key；切换备用模型`LLM_PROVIDER`；查看`/metrics`中`metapivot_llm_calls_total{status="failed"}` |
| 知识库检索慢 | 检查Milvus状态；调整`EMBEDDING_BATCH_SIZE` |
| 任务指标不出现 | 等任务到终态（`finished_at`非空）再查`/metrics`；FAILED 任务也会采集 |
| JSON 日志解析失败 | 确认 `APP_LOG_FORMAT=json`；每行独立 JSON，使用 `jq .` 解析 |

## 七、监控与可观测性

### Prometheus 指标端点

服务自动暴露 `/metrics` 端点，提供 5 组业务指标（HTTP/Agent/LLM/Skill/Workflow），可直接配置 Prometheus 抓取：

```yaml
# prometheus.yml
scrape_configs:
  - job_name: metapivot
    scrape_interval: 15s
    metrics_path: /metrics
    static_configs:
      - targets: ['metapivot-host:8000']
```

**关键指标速查**：
- `metapivot_http_requests_total` — HTTP 请求计数（method/path/status）
- `metapivot_agent_tasks_total{status="completed|failed|cancelled"}` — Agent 任务成功率
- `metapivot_agent_active_tasks` — 当前活跃任务数（Gauge）
- `metapivot_llm_calls_total` — LLM 调用计数（model/status）
- `metapivot_agent_token_usage_total{type="prompt|completion|total"}` — Token 用量

**路径归一化**：动态 ID 路径自动归一化为 `{id}`（如 `/api/v1/agent/tasks/abc-123` → `/api/v1/agent/tasks/{id}`），避免 Prometheus 标签高基数。

### 结构化 JSON 日志

生产环境推荐 `APP_LOG_FORMAT=json` 输出单行 JSON（ELK/Loki 友好）：

```bash
# .env
APP_LOG_FORMAT=json         # text（开发）/ json（生产）
APP_LOG_LEVEL=INFO
APP_LOG_RETENTION_DAYS=3    # 文件轮转保留天数
```

每条日志字段：`ts` / `level` / `logger` / `module` / `function` / `line` / `message` / `extra`（含 `request_id`/`trace_id`/`user_id`）/ `exception`。

**日志采集**（Loki Promtail 示例）：
```yaml
scrape_configs:
  - job_name: metapivot
    static_configs:
      - targets: [localhost]
        labels:
          job: metapivot
          __path__: /app/logs/app_*.log
```

### 任务详情接口增强

`GET /api/v1/agent/tasks/{task_id}` 响应新增字段（便于追踪任务耗时）：

```json
{
  "task_id": "task_xxx",
  "status": "completed",
  "total_tokens": 1234,
  "created_at": "2026-07-04T10:00:00",
  "updated_at": "2026-07-04T10:00:05",
  "finished_at": "2026-07-04T10:00:05.123",
  "duration_ms": 5123,
  "steps": [...]
}
```

`finished_at` 在 `_run_task` 的 `update_task_status` 中设置（接近终态），`duration_ms` 由 ORM 计算。判断任务是否真正结束应查 `finished_at` 非空，而非 `status` 字段。

## 八、下一步

- 阅读 [架构设计](architecture.md) 了解系统全貌
- 阅读 [API规范](api-spec.md) 接入所有端点
- 阅读 [数据模型](data-model.md) 了解存储结构
- 阅读 [生产级升级路线图](production-readiness.md) 了解监控/告警/Celery 等下一步
- 配置工作流编排器创建第一个工作流
- 通过MCP接入企业内部系统API
