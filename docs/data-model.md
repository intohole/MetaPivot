# MetaPivot 数据模型设计

> PostgreSQL 元数据库 + Redis 缓存 + Milvus 向量库

## 一、PostgreSQL 表结构（元数据/审计/工作流）

### 1. 用户与权限

```sql
-- 用户表
CREATE TABLE users (
    id VARCHAR(36) PRIMARY KEY,
    username VARCHAR(64) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'user',  -- user/manager/admin
    im_accounts JSONB DEFAULT '{}',             -- {dingtalk: "uid1", wecom: "uid2", feishu: "uid3"}
    status VARCHAR(20) NOT NULL DEFAULT 'active',  -- active/disabled
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_users_username ON users(username);
CREATE INDEX idx_users_status ON users(status);

-- 角色表
CREATE TABLE roles (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(64) UNIQUE NOT NULL,
    description TEXT,
    permissions JSONB NOT NULL DEFAULT '[]',   -- 权限标签数组
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- 审计日志表（关键，留存6个月+）
CREATE TABLE audit_logs (
    id BIGSERIAL PRIMARY KEY,
    request_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(36),
    action VARCHAR(64) NOT NULL,                -- agent_chat/workflow_execute/skill_call
    skill_id VARCHAR(36),
    workflow_id VARCHAR(36),
    task_id VARCHAR(36),
    input_hash VARCHAR(64) NOT NULL,           -- 输入SHA256（不存原文，脱敏）
    output_summary TEXT,                         -- 输出摘要（≤500字）
    duration_ms INTEGER,
    status VARCHAR(20) NOT NULL,                 -- success/failed/rejected
    error_code VARCHAR(64),
    error_message TEXT,
    ip_address VARCHAR(45),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_audit_user_time ON audit_logs(user_id, created_at);
CREATE INDEX idx_audit_action_time ON audit_logs(action, created_at);
CREATE INDEX idx_audit_skill_time ON audit_logs(skill_id, created_at);
CREATE INDEX idx_audit_created_at ON audit_logs(created_at);
```

### 2. Skill与MCP

```sql
-- Skill注册表
CREATE TABLE skills (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(128) UNIQUE NOT NULL,
    description TEXT NOT NULL,                  -- LLM可读描述
    input_schema JSONB NOT NULL,                -- JSON Schema参数定义
    source_type VARCHAR(20) NOT NULL,           -- mcp/function/workflow
    source_ref VARCHAR(255) NOT NULL,           -- MCP Server名/函数路径/工作流ID
    permission VARCHAR(64) DEFAULT 'user',      -- 所需权限标签
    require_confirm BOOLEAN NOT NULL DEFAULT FALSE,  -- 是否需HITL
    tags JSONB DEFAULT '[]',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    call_count BIGINT DEFAULT 0,
    last_called_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_skills_enabled ON skills(enabled);
CREATE INDEX idx_skills_source ON skills(source_type, source_ref);

-- MCP Server注册表
CREATE TABLE mcp_servers (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(128) UNIQUE NOT NULL,
    transport VARCHAR(20) NOT NULL,             -- stdio/http
    endpoint VARCHAR(512),                      -- http模式URL；stdio模式命令
    args JSONB DEFAULT '[]',                    -- stdio启动参数
    env JSONB DEFAULT '{}',                     -- 环境变量（密钥引用，不存明文）
    auth_type VARCHAR(20),                      -- none/api_key/oauth
    auth_secret_ref VARCHAR(128),               -- 密钥引用名（指向配置中心）
    status VARCHAR(20) NOT NULL DEFAULT 'stopped',  -- stopped/running/error
    last_ping_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

### 3. 工作流

```sql
-- 工作流定义
CREATE TABLE workflows (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    description TEXT,
    definition JSONB NOT NULL,                  -- {nodes:[], edges:[], variables:[]}
    trigger JSONB DEFAULT '{}',                 -- {type: "manual"|"im_keyword"|"schedule", config:{}}
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    version INTEGER NOT NULL DEFAULT 1,
    created_by VARCHAR(36),
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_workflows_enabled ON workflows(enabled);

-- 工作流执行实例
CREATE TABLE workflow_executions (
    id VARCHAR(36) PRIMARY KEY,
    workflow_id VARCHAR(36) NOT NULL REFERENCES workflows(id),
    status VARCHAR(20) NOT NULL,                -- pending/running/paused/completed/failed/cancelled
    current_node VARCHAR(128),
    inputs JSONB DEFAULT '{}',
    outputs JSONB DEFAULT '{}',
    triggered_by VARCHAR(36),                   -- user_id
    trigger_channel VARCHAR(20),                 -- api/dingtalk/wecom/feishu
    chat_id VARCHAR(128),
    checkpoint_id VARCHAR(128),                -- LangGraph checkpoint引用
    error JSONB,
    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMP,
    FOREIGN KEY (workflow_id) REFERENCES workflows(id)
);
CREATE INDEX idx_exec_workflow_status ON workflow_executions(workflow_id, status);
CREATE INDEX idx_exec_status_time ON workflow_executions(status, started_at);
```

### 4. Agent任务

```sql
-- Agent任务表
CREATE TABLE agent_tasks (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36),
    channel VARCHAR(20) NOT NULL,               -- api/dingtalk/wecom/feishu
    chat_id VARCHAR(128),
    original_message TEXT,                       -- 原始用户消息
    intent VARCHAR(64),                          -- 识别意图
    mode VARCHAR(20) NOT NULL DEFAULT 'agent',   -- pipeline/workflow/agent
    status VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending/planning/executing/waiting_confirm/completed/failed/cancelled
    plan JSONB,                                  -- Planner生成的步骤列表
    current_step INTEGER DEFAULT 0,
    result JSONB,
    checkpoint_id VARCHAR(128),                -- LangGraph checkpoint
    error JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_tasks_user_status ON agent_tasks(user_id, status);
CREATE INDEX idx_tasks_status_time ON agent_tasks(status, created_at);

-- Agent任务步骤表
CREATE TABLE agent_task_steps (
    id BIGSERIAL PRIMARY KEY,
    task_id VARCHAR(36) NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL,
    step_name VARCHAR(128),
    tool_name VARCHAR(128),
    tool_input JSONB,
    tool_output JSONB,
    require_confirm BOOLEAN DEFAULT FALSE,
    confirm_decision VARCHAR(20),               -- approve/reject/modify
    confirm_user VARCHAR(36),
    confirm_at TIMESTAMP,
    status VARCHAR(20) NOT NULL,                -- pending/running/success/failed/skipped
    duration_ms INTEGER,
    error TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    FOREIGN KEY (task_id) REFERENCES agent_tasks(id)
);
CREATE INDEX idx_steps_task ON agent_task_steps(task_id, step_index);
```

### 5. 知识库与IM

```sql
-- 知识库文档表
CREATE TABLE knowledge_documents (
    id VARCHAR(36) PRIMARY KEY,
    filename VARCHAR(255) NOT NULL,
    file_path VARCHAR(512),                     -- 对象存储路径
    file_size BIGINT,
    mime_type VARCHAR(100),
    chunk_count INTEGER DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'processing',  -- processing/ready/error
    metadata JSONB DEFAULT '{}',
    created_by VARCHAR(36),
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_docs_status ON knowledge_documents(status);

-- IM会话表
CREATE TABLE im_chats (
    id VARCHAR(128) PRIMARY KEY,                 -- 统一会话ID
    channel VARCHAR(20) NOT NULL,               -- dingtalk/wecom/feishu
    original_chat_id VARCHAR(128) NOT NULL,     -- IM平台原始chat_id
    chat_type VARCHAR(20) NOT NULL,             -- single/group
    title VARCHAR(255),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(channel, original_chat_id)
);

-- IM消息记录表
CREATE TABLE im_messages (
    id VARCHAR(128) PRIMARY KEY,                 -- 统一消息ID
    channel VARCHAR(20) NOT NULL,
    original_msg_id VARCHAR(128) NOT NULL,
    chat_id VARCHAR(128) NOT NULL,
    sender_id VARCHAR(128) NOT NULL,
    sender_name VARCHAR(128),
    content TEXT,
    message_type VARCHAR(20) NOT NULL,          -- text/card/image/file
    raw_payload JSONB,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE(channel, original_msg_id)
);
CREATE INDEX idx_im_messages_chat_time ON im_messages(chat_id, created_at);
```

### 6. 系统配置

```sql
-- 系统配置表
CREATE TABLE configs (
    key VARCHAR(128) PRIMARY KEY,
    value TEXT NOT NULL,
    category VARCHAR(64) NOT NULL,              -- llm/im/mcp/security
    description TEXT,
    updatable BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- 密钥引用表（不存明文，指向环境变量或KMS）
CREATE TABLE secret_refs (
    name VARCHAR(128) PRIMARY KEY,              -- 引用名
    source VARCHAR(20) NOT NULL,                -- env/kms/file
    reference VARCHAR(512) NOT NULL,            -- 环境变量名/KMS keyId/文件路径
    description TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
```

## 二、Redis 数据结构（缓存/会话/限流）

| Key模式 | 类型 | TTL | 用途 |
|---------|------|-----|------|
| `session:{user_id}` | Hash | 24h | 用户会话（JWT黑名单/刷新） |
| `agent:task:{task_id}:state` | Hash | 2h | Agent任务临时状态（LangGraph checkpoint缓存） |
| `agent:memory:{user_id}` | List | 24h | 短期对话记忆（最近20轮） |
| `workflow:exec:{exec_id}:state` | Hash | 2h | 工作流执行临时状态 |
| `ratelimit:{channel}:{chat_id}` | String+INCR | 1s/60s | 令牌桶限流 |
| `skill:registry:cache` | Hash | 60s | Skill Registry本地缓存（Pub/Sub刷新） |
| `mcp:server:{name}:status` | Hash | 30s | MCP Server连接状态 |
| `im:token:{channel}` | String | 7200s | IM平台access_token缓存 |
| `lock:task:{task_id}` | String+NX | 30s | 任务执行分布式锁 |
| `pubsub:skill:changed` | Pub/Sub | - | Skill变更通知通道 |

## 三、Milvus 向量库（RAG知识库）

```python
# Collection: knowledge_chunks
{
    "id": "string",              # chunk唯一ID
    "document_id": "string",     # 所属文档ID
    "content": "string",          # 文本内容
    "embedding": "float_vector",  # 1536维（依赖Embedding模型）
    "metadata": {
        "filename": "string",
        "page": "int",
        "chunk_index": "int",
        "source": "string",
        "created_at": "string"
    }
}
# 索引: IVF_FLAT + Cosine
```

## 四、数据保留与清理策略

| 数据 | 保留期 | 清理方式 |
|------|--------|----------|
| 审计日志 | 6个月+（合规要求） | 归档到冷存储后删除热数据 |
| Agent任务 | 30天 | 定时任务清理 |
| IM消息记录 | 30天 | 定时任务清理（仅元数据，原始消息在IM平台） |
| Redis临时状态 | 2小时 | TTL自动过期 |
| 日志文件 | 3天（用户规则） | loguru文件轮转 |
| 知识库向量 | 随文档生命周期 | 文档删除时同步删除向量 |

## 五、关键索引与性能

- 所有外键和查询高频字段建立索引
- 审计日志按`created_at`分区（按月分区，便于归档）
- `audit_logs` 表预计写入量大，采用`BIGSERIAL`主键+时间分区
- `agent_tasks` 按`status`建立部分索引（仅活跃状态）
- 工作流`definition`使用JSONB（支持Gin索引，便于查询节点）
