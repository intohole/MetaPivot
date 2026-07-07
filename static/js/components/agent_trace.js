/* ============================================================
   agent_trace.js — Agent 执行轨迹时间线组件
   挂载：window.Components.AgentTrace
   消费 agent.js 转换后的 trace steps（来自 SSE / replay API）
   消除 agent 黑盒：展示 intent/planning/tool_call/llm/reflected/trimmed
   Props:
     - steps: Array [{type, step, tool, args, status, result, error, duration_ms, tokens, thought, timestamp}]
     - streamingText: String  流式 reply 累积文本
     - streaming: Boolean     是否正在流式输出
   复用：StatusBadge / EmptyState / renderMarkdown
   ============================================================ */
(function () {
  const { computed } = Vue
  const Components = window.Components || (window.Components = {})

  // 步骤类型 → 图标映射
  const ICON_MAP = {
    step_intent: '🔍', step_planning: '📋', step_execute: '⚙️',
    tool: '🔧', blocked: '⚠️', trimmed: '✂️', reflected: '💭', llm: '🤖', step: '•'
  }

  // 安全 JSON 格式化（截断超长输出）
  function fmtJson(obj, max = 800) {
    if (obj == null) return ''
    let s = ''
    try { s = typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2) } catch (e) { s = String(obj) }
    return s.length > max ? s.slice(0, max) + '\n...[truncated]' : s
  }

  Components.AgentTrace = {
    name: 'AgentTrace',
    props: {
      steps: { type: Array, default: () => [] },
      streamingText: { type: String, default: '' },
      streaming: { type: Boolean, default: false }
    },
    setup(props) {
      const renderMarkdown = window.renderMarkdown || ((t) => t)

      // 步骤标题
      const titleOf = (s) => {
        if (s.type === 'tool') return '调用工具：' + (s.tool || '?')
        if (s.type === 'blocked') return '拦截工具：' + (s.tool || '?')
        if (s.type === 'llm') return 'LLM 推理'
        if (s.type === 'reflected') return '反思校正'
        if (s.type === 'trimmed') return '上下文裁剪'
        if (s.type === 'step' || s.type?.startsWith('step_')) {
          const name = s.step || s.type.replace('step_', '')
          return ({ intent: '意图识别', planning: '任务规划', execute: '执行步骤' })[name] || ('步骤：' + name)
        }
        return s.type || '步骤'
      }

      // 图标
      const iconOf = (s) => {
        if (s.type === 'step') return ICON_MAP['step_' + (s.step || '')] || ICON_MAP.step
        return ICON_MAP[s.type] || '•'
      }

      // 状态映射到 StatusBadge
      const statusOf = (s) => {
        if (s.type === 'tool') return s.status === 'completed' ? 'success' : s.status === 'failed' ? 'error' : 'info'
        if (s.type === 'blocked') return 'warning'
        if (s.type === 'reflected') return 'info'
        return ''
      }

      // 是否默认展开（当前执行中 / 失败 / 反思）
      const defaultOpen = (s, idx) => {
        if (s.type === 'reflected' || s.type === 'blocked') return true
        if (s.type === 'tool' && (s.status === 'failed' || s.status === 'started')) return true
        return idx === props.steps.length - 1 && props.streaming
      }

      // 汇总：总 Token / 总耗时
      const totalTokens = computed(() =>
        props.steps.reduce((sum, s) => sum + (s.tokens || 0), 0)
      )
      const totalDuration = computed(() =>
        props.steps.reduce((sum, s) => sum + (s.duration_ms || 0), 0)
      )
      const hasContent = computed(() =>
        props.steps.length > 0 || props.streamingText || props.streaming
      )

      return { renderMarkdown, titleOf, iconOf, statusOf, defaultOpen, fmtJson, totalTokens, totalDuration, hasContent }
    },
    template: `
      <div v-if="hasContent" class="agent-trace">
        <!-- 汇总条 -->
        <div class="trace-summary">
          <span class="text-xs text-ink-muted">📋 {{ steps.length }} 步</span>
          <span v-if="totalDuration > 0" class="text-xs text-ink-muted">⏱ {{ (totalDuration / 1000).toFixed(1) }}s</span>
          <span v-if="totalTokens > 0" class="text-xs text-ink-muted">🎯 {{ totalTokens }} tokens</span>
        </div>

        <!-- 时间线 -->
        <ol class="trace-timeline">
          <li v-for="(s, i) in steps" :key="i" class="trace-item">
            <span class="trace-dot" aria-hidden="true">{{ iconOf(s) }}</span>
            <details class="trace-card" :open="defaultOpen(s, i)">
              <summary class="trace-head">
                <span class="text-sm font-medium text-ink">{{ titleOf(s) }}</span>
                <span class="trace-meta">
                  <status-badge v-if="statusOf(s)" :status="statusOf(s)" />
                  <span v-if="s.duration_ms" class="text-xs text-ink-subtle">{{ s.duration_ms }}ms</span>
                  <span v-if="s.tokens" class="text-xs text-ink-subtle">{{ s.tokens }} tok</span>
                </span>
              </summary>
              <div v-if="s.type === 'tool' || s.type === 'blocked'" class="trace-body">
                <div v-if="s.args"><p class="trace-label">入参</p><pre class="trace-code">{{ fmtJson(s.args) }}</pre></div>
                <div v-if="s.result != null"><p class="trace-label">结果</p><pre class="trace-code">{{ fmtJson(s.result) }}</pre></div>
                <div v-if="s.error"><p class="trace-label text-danger">错误</p><pre class="trace-code trace-code-error">{{ fmtJson(s.error) }}</pre></div>
                <div v-if="s.reason"><p class="trace-label text-warning">拦截原因</p><p class="text-sm text-ink">{{ s.reason }}</p></div>
              </div>
              <div v-else-if="s.type === 'reflected'" class="trace-body">
                <p class="text-sm text-ink">{{ s.thought || s.correction_hint || '已根据上一步结果校正执行方向' }}</p>
              </div>
              <div v-else-if="s.type === 'trimmed'" class="trace-body">
                <p class="text-sm text-ink-muted">消息从 {{ s.before }} 条裁剪到 {{ s.after }} 条（保护上下文窗口）</p>
              </div>
              <div v-else-if="s.result" class="trace-body">
                <pre class="trace-code">{{ fmtJson(s.result) }}</pre>
              </div>
            </details>
          </li>
        </ol>

        <!-- 流式 reply 区 -->
        <div v-if="streamingText || streaming" class="trace-streaming">
          <div class="markdown-body text-sm text-ink" v-html="renderMarkdown(streamingText || '')"></div>
          <span v-if="streaming" class="trace-cursor" aria-hidden="true">▋</span>
        </div>
      </div>
    `
  }
})()
