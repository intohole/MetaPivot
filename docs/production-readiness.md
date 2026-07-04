# MetaPivot 生产级升级路线图

> 基于 Phase 4 交付的可运行版本，给出三维度（安全/性能/可用性）生产级升级路线。
> 每项标注：当前状态 / 目标状态 / 实施优先级 / 工作量估算。

## 当前生产就绪度自评

| 维度 | 已具备 | 待补强 |
|------|--------|--------|
| 安全 | JWT认证、RBAC、HITL、审计日志、密钥环境变量化 | 数据脱敏、Guardrail、密钥轮换、等保合规 |
| 性能 | 全异步架构、async SQLAlchemy、Redis缓存、后台任务 | 连接池调优、SSE卡片渐进、消息合并、限流落地 |
| 可用性 | /health、/ready探针、结构化JSON日志、Prometheus指标、/metrics端点、docker-compose | Grafana看板、Alertmanager告警、Celery队列、回滚机制、灰度发布 |

---

## 一、安全升级（P0）

### 1.1 数据脱敏与 Guardrail ✅ 已实施
- **当前**：Guardrail 已覆盖 LLM 输入/输出全链路
- **实施细节**：
  - PII 脱敏 4 类：身份证 / 手机号 / 邮箱 / **银行卡（Luhn 校验通过才脱敏，避免误伤长数字）**
  - prompt injection 命中即阻断（返回安全文本，不抛异常），覆盖 6 个 pattern（中英文）
  - 敏感关键词 11 个（jwt_secret / encrypt_key / api_key / DATABASE_URL / IM 密钥等），输出经 `sanitize_output` 替换为 `***`
  - 两处输出路径统一脱敏：`replier_node`（非流式）+ `_stream_final_reply`（流式）
- **优先级**：P0（涉及数据合规）
- **工作量**：2 天

### 1.2 密钥管理与轮换 ✅ 已实施
- **当前**：JWT kid 多密钥并行校验 + AES-CBC-256 随机 IV
- **实施细节**：
  - `create_access_token` 注入 `kid` header 标识当前主密钥
  - `decode_access_token` 支持 kid 多密钥并行校验（primary/previous）+ 向后兼容（无 kid 走 primary）+ 主密钥失败 fallback previous（grace period）
  - 轮换流程：配置 `JWT_SECRET=新密钥` + `JWT_SECRET_PREVIOUS=旧密钥`，旧 token 过期后清空 `JWT_SECRET_PREVIOUS`
  - AES-CBC-256 加密：随机 IV（`os.urandom(16)`）前置于密文 + SHA-256 派生密钥 + `decrypt_aes` 配对函数
- **优先级**：P0
- **工作量**：1.5 天

### 1.3 等保 2.0 三级合规
- **当前**：架构设计已考虑（审计≥6月、全内网、权限分级），但未做正式测评
- **目标**：通过等保 2.0 三级测评
- **实施要点**：
  - 审计日志独立库存储，防篡改（追加 WORM 存储）
  - 数据库 TDE 透明加密
  - 双因子认证（管理员后台）
  - 安全审计接入 SIEM
- **优先级**：P1（取决于企业合规要求）
- **工作量**：5-10 天（含整改）

---

## 二、性能升级（P1）

### 2.1 数据库连接池调优
- **当前**：`app/infra/db/session.py` 使用默认连接池参数
- **目标**：根据生产负载调优
  ```python
  create_async_engine(
      DATABASE_URL,
      pool_size=20,           # 常驻连接
      max_overflow=10,        # 突发连接
      pool_pre_ping=True,     # 心跳检测
      pool_recycle=1800,      # 30分钟回收
  )
  ```
- **优先级**：P1
- **工作量**：0.5 天 + 压测验证

### 2.2 IM 响应时延优化（3-5 秒超时）
- **当前**：异步架构已立即 ACK，但 LLM 首字延迟可能 5-10 秒
- **目标**：IM 3 秒内推送"处理中"卡片，最终结果异步更新
- **实施**：
  - 接收消息后立即发送"思考中"互动卡片
  - LLM 流式输出 → SSE 推送 → 卡片渐进更新
  - 钉钉 `update_card` / 飞书 `patch_message` / 企微 `update_template_card`
- **优先级**：P1（直接影响用户体验）
- **工作量**：3 天

### 2.3 限流落地 ✅ 已实施
- **当前**：用户维度限流中间件已实施，Redis Lua 令牌桶 + Memory 滑动窗口双 backend
- **实施细节**：
  - Redis Lua 真令牌桶（原子 refill + consume + retry_after 计算），替代 INCR+EXPIRE 固定窗口
  - 限流维度：优先 `user:{jwt_sub}`（从 Bearer token 解码），无 token 走 `ip:{client_ip}`，避免多账号绕过
  - 动态 Retry-After（429 响应 header + JSON body），客户端按值退避
  - Memory backend 滑动窗口适配，单进程下更精确
  - 缓存故障降级为不限流（避免拖垮服务）
- **优先级**：P1
- **工作量**：1 天

### 2.4 消息合并与去重
- **当前**：每条消息独立处理，无去重
- **目标**：
  - 同 chat_id 短时间多消息合并为一次 Agent 调用
  - Redis SETNX 做幂等去重（msg_id，5 分钟）
- **优先级**：P2
- **工作量**：1 天

---

## 三、可用性升级（P1）

### 3.1 监控与告警
- **当前**：已落地结构化 JSON 日志（`APP_LOG_FORMAT=json`，ELK/Loki 友好）+ Prometheus 指标体系
  - `/metrics` 端点暴露 Prometheus 文本格式（`prometheus_client==0.21.1`）
  - 5 组业务指标：HTTP 请求计数/延迟、Agent 任务计数/延迟/活跃数/Token 用量、LLM 调用计数/延迟、Skill 调用计数、Workflow 执行计数
  - HTTP 中间件自动采集 `method/path/status` 维度，路径归一化（动态 ID → `{id}`）避免高基数
  - 节点级事件（step_started/llm_call/stuck_detected 等）drain 到 SSE，链路可见
  - AgentTaskORM 增加 `finished_at`/`duration_ms` 字段，任务详情接口返回
- **目标**：
  - Grafana 看板（基于现有 5 组指标）+ Alertmanager 告警（钉钉/企微机器人通知）
  - 关键告警规则：LLM P99 > 10s、Agent 失败率 > 5%、活跃任务数 > 50
- **实施**：
  - 已完成：`app/utils/metrics.py`（指标定义）+ `app/middleware/metrics_middleware.py`（HTTP 采集）+ 各 Service finally 块注入业务指标
  - 待补：Grafana 看板 JSON（`docs/grafana/metapivot-dashboard.json`）+ Alertmanager rule 文件
- **优先级**：P2（监控已落地，仅需配置看板与告警规则）
- **工作量**：0.5 天（看板 + 告警规则）

### 3.2 Celery 任务队列落地
- **当前**：`docker-compose.yml` 已定义 worker 服务，但 `app/infra/queue/celery_app.py` 未实现
- **目标**：
  - 长耗时任务（文档分块、批量知识入库）走 Celery
  - Agent 任务可降级为 Celery（当前用 asyncio.create_task，单进程重启会丢任务）
- **实施**：
  ```python
  # app/infra/queue/celery_app.py（新建）
  from celery import Celery
  celery_app = Celery("metapivot", broker=redis_url, backend=redis_url)
  celery_app.conf.task_routes = {
      "app.service.knowledge.*": {"queue": "knowledge"},
      "app.service.agent.*": {"queue": "agent"},
  }
  ```
- **优先级**：P1（影响任务可靠性）
- **工作量**：2 天

### 3.3 回滚机制
- **当前**：Skill 执行失败无自动回滚
- **目标**：关键 Skill（写操作）定义 `rollback_handler`
  ```python
  # Skill 配置增加 rollback_ref 字段
  class Skill:
      rollback_ref: Optional[str]  # 回滚函数引用
  ```
  - 执行前快照状态，失败/拒绝时调用 rollback
- **优先级**：P2
- **工作量**：2 天

### 3.4 灰度发布
- **当前**：全量发布
- **目标**：
  - Skill 灰度：按用户白名单/部门灰度新 Skill
  - Agent 模式灰度：10% 流量用新 Prompt
- **实施**：Skill 表增加 `rollout_percentage` 字段 + ConfigService 灰度判断
- **优先级**：P2
- **工作量**：1.5 天

---

## 四、已识别技术债（来自 Code Review）

### 4.1 Domain 层依赖注入净化（P2）
- **现状**：`domain/agent/nodes.py` 和 `domain/workflow/engine.py` 通过函数内延迟导入调用 Service 层（`skill_service`、`channel_service`）
- **影响**：架构上属 Domain→Service 向上依赖，违反"依赖方向只允许向下"规则（运行时妥协，无循环导入）
- **改造方向**：
  ```python
  # app/domain/agent/runtime.py（新建 Protocol）
  from typing import Protocol
  class ToolRuntime(Protocol):
      async def list_tools_for_llm(self, permission: str) -> list: ...
      async def execute(self, skill_id: str, args: dict, user_id: str) -> dict: ...
      async def get_skill(self, skill_id: str): ...
      async def find_skill_id_by_name(self, name: str) -> Optional[str]: ...
  
  # run_agent(state, runtime) 接收 runtime，节点通过 state.runtime 调用
  ```
- **工作量**：1 天（触及 nodes/graph/agent_service/workflow_service 5 文件）

### 4.2 向量库 RAG 接入（P1）
- **现状**：`infra/rag/search.py` 仅做关键词匹配兜底，未接入 Milvus
- **目标**：
  - 文档分块 → embedding（bge-m3 或 text-embedding-3-small）
  - Milvus 向量检索 + 关键词混合召回
- **工作量**：2 天

### 4.3 三端 IM 卡片实现（P1）
- **现状**：`send_card` / `update_card` 在三个适配器中返回 "Not implemented in MVP"
- **目标**：实现钉钉 STREAM 卡片 / 飞书回传 / 企微模板卡片
- **工作量**：3 天

---

## 五、升级优先级路线图

```
Week 1 (P0 安全):
  ├─ 1.1 Guardrail 输入输出脱敏
  ├─ 1.2 密钥轮换机制
  └─ 2.3 限流中间件落地

Week 2 (P1 性能+可用性):
  ├─ 2.1 连接池调优
  ├─ 2.2 IM 响应时延优化（思考中卡片）
  ├─ 3.1 Grafana 看板 + Alertmanager 告警（指标已落地，仅需配置）
  └─ 3.2 Celery 任务队列落地

Week 3 (P1 功能补强):
  ├─ 4.2 Milvus RAG 接入
  ├─ 4.3 三端 IM 卡片实现
  └─ 4.1 Domain DI 净化（架构债）

Week 4 (P2 增强 + 合规):
  ├─ 1.3 等保 2.0 整改
  ├─ 2.4 消息合并去重
  ├─ 3.3 回滚机制
  └─ 3.4 灰度发布
```

## 六、端到端测试执行清单

> 冒烟测试已验证应用骨架可启动（5/5 通过）。完整端到端测试需在本地启动 docker-compose 后执行：

```bash
# 1. 启动依赖
docker-compose up -d postgres redis milvus

# 2. 初始化数据库（含种子数据：admin 用户 + 默认 Skill）
docker-compose exec api python -m app.infra.db.init_db

# 3. 启动应用
docker-compose up -d api

# 4. 端到端测试用例
#   4.1 认证：POST /api/v1/auth/token 获取 token
curl -X POST http://localhost:8000/api/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"changeme"}'

#   4.2 Agent 对话：POST /api/v1/agent/chat
curl -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"message":"现在几点","channel":"api","chat_id":"test_1"}'

#   4.3 Skill 管理：GET /api/v1/skills
curl http://localhost:8000/api/v1/skills -H "Authorization: Bearer <token>"

#   4.4 工作流执行：POST /api/v1/workflows/{id}/execute
#   4.5 知识库上传：POST /api/v1/knowledge/documents
#   4.6 审计查询：GET /api/v1/audit/logs

# 5. IM 渠道测试（需配置真实凭证）
#   钉钉：配置 DINGTALK_CLIENT_ID/SECRET，企业内部应用 @机器人
#   飞书：配置 FEISHU_APP_ID/SECRET，自建应用开启消息卡片
#   企微：配置 WECOM_CORP_ID/SECRET/TOKEN/AES_KEY，自建应用回调
```

## 七、验收标准

| 维度 | 达标标准 |
|------|----------|
| 安全 | Guardrail 覆盖 100% LLM 调用 + 密钥 90 天轮换 + 审计日志独立存储 |
| 性能 | IM 3 秒内首响应 + DB 连接池配置合理 + 限流中间件生效 |
| 可用性 | Prometheus 指标已暴露（HTTP/Agent/LLM/Skill/Workflow 5 组）+ 结构化 JSON 日志 ELK 友好 + 关键告警接入 IM + Celery 任务可靠执行 |
| 代码质量 | 文件 ≤300 行 + 分层依赖向下 + 异步非阻塞 + Domain DI 净化 |
