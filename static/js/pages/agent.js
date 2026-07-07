/* Agent 任务页 — 对话界面 + 任务历史 + SSE 实时流式订阅 + Markdown 渲染 */
(function () {
  const { ref, reactive, onMounted, computed, nextTick } = Vue
  window.Pages = window.Pages || {}

  window.Pages.Agent = {
    name: 'AgentPage',
    setup() {
      const state = window.AppState
      const messages = reactive([])  // {role, content, time}
      const inputMsg = ref('')
      const currentTaskId = ref('')
      const taskStatus = ref('')
      const taskSteps = reactive([])
      const waitingConfirm = ref(false)
      const history = ref([])
      const streaming = ref(false)
      const streamingText = ref('')  // 流式 reply 累积文本（token 事件拼接）
      const chatBox = ref(null)
      let abortSSE = null  // SSE 订阅取消函数

      const columns = [
        { key: 'task_id', label: '任务ID', width: '120px' },
        { key: 'status', label: '状态', width: '100px' },
        { key: 'created_at', label: '时间', width: '150px' }
      ]

      const canSend = computed(() => inputMsg.value.trim() && !streaming.value)
      const renderMarkdown = window.renderMarkdown || ((t) => t)

      const loadHistory = async () => {
        try {
          const res = await window.API.get('/agent/tasks', { page: 1, page_size: 20 })
          history.value = res.items || []
        } catch (e) {
          state.notify('加载历史失败：' + (e.message || '未知错误'), 'error')
        }
      }

      const scrollToBottom = () => {
        nextTick(() => { if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight })
      }

      const sendMessage = async () => {
        if (!canSend.value) return
        const msg = inputMsg.value.trim()
        messages.push({ role: 'user', content: msg, time: new Date().toLocaleTimeString() })
        inputMsg.value = ''
        streaming.value = true
        streamingText.value = ''
        taskStatus.value = 'pending'
        taskSteps.length = 0

        try {
          // Phase C1: stream=true 触发后端返回 task_id，前端再走 SSE 订阅
          const data = await window.API.post('/agent/chat', {
            message: msg, channel: 'api', stream: true
          })
          currentTaskId.value = data.task_id
          state.notify('Agent 任务已创建：' + data.task_id.slice(0, 8), 'info')
          // SSE 流式订阅（替代 2s 轮询）
          const streamPath = '/agent/tasks/' + data.task_id + '/stream'
          abortSSE = window.API.streamSSE(streamPath, onSSEEvent, onSSEError, onSSEClose)
        } catch (e) {
          streaming.value = false
          // 发送失败回滚用户消息，避免"有问无答"困惑
          const idx = messages.findIndex(m => m.content === msg && m.role === 'user')
          if (idx >= 0) messages.splice(idx, 1)
          inputMsg.value = msg  // 回填输入框，方便用户重试
          state.notify('发送失败：' + (e.message || '未知错误'), 'error')
        }
      }

      // 通用：事件 → trace step（live SSE + replay 共用，消除重复）
      const pushStep = (type, d) => {
        switch (type) {
          case 'step_completed':
            if (d.step) taskSteps.push({ type: 'step', step: String(d.step).replace(/_\d+$/, ''), result: d.result, duration_ms: d.duration_ms || 0 })
            break
          case 'llm_call':
            taskSteps.push({ type: 'llm', duration_ms: d.duration_ms || 0, tokens: (d.usage && d.usage.total_tokens) || 0, result: d.usage })
            break
          case 'tool_call':
            if (d.status === 'started') {
              taskSteps.push({ type: 'tool', tool: d.tool, args: d.args, status: 'started' })
            } else {
              const last = [...taskSteps].reverse().find(s => s.type === 'tool' && s.tool === d.tool && s.status === 'started')
              if (last) { last.status = d.status; last.result = d.result; last.error = d.error; last.duration_ms = d.duration_ms || 0 }
              else taskSteps.push({ type: 'tool', tool: d.tool, args: d.args, status: d.status, result: d.result, error: d.error })
            }
            break
          case 'tool_blocked': taskSteps.push({ type: 'blocked', tool: d.tool, reason: d.reason }); break
          case 'context_trimmed': taskSteps.push({ type: 'trimmed', before: d.before, after: d.after }); break
          case 'reflected': taskSteps.push({ type: 'reflected', thought: d.correction_hint || d.thought }); break
        }
        scrollToBottom()
      }

      // SSE 事件处理 — 全事件消费（消除 agent 黑盒）
      const onSSEEvent = (ev) => {
        let data = {}
        try { data = JSON.parse(ev.data || '{}') } catch (e) { data = {} }
        switch (ev.event) {
          case 'step_started': taskStatus.value = 'executing'; break
          case 'step_completed': taskStatus.value = 'executing'; pushStep('step_completed', data); break
          case 'llm_call': pushStep('llm_call', data); break
          case 'tool_call': pushStep('tool_call', data); break
          case 'tool_blocked': pushStep('tool_blocked', data); break
          case 'context_trimmed': pushStep('context_trimmed', data); break
          case 'reflected': pushStep('reflected', data); break
          case 'token': streamingText.value += data.text || ''; scrollToBottom(); break
          case 'human_confirm_required': waitingConfirm.value = true; taskStatus.value = 'waiting_confirm'; break
          case 'final_result':
            streaming.value = false; taskStatus.value = 'completed'
            const answer = streamingText.value || data.content || data.answer || ''
            if (answer) messages.push({ role: 'assistant', content: answer, time: new Date().toLocaleTimeString() })
            streamingText.value = ''; scrollToBottom(); loadHistory()
            showPostActions.value = true  // Phase 3: 任务完成后快捷动作
            break
          case 'error':
            streaming.value = false; taskStatus.value = 'failed'
            messages.push({ role: 'assistant', content: '⚠️ 任务失败：' + (data.message || '未知错误'), time: new Date().toLocaleTimeString() })
            streamingText.value = ''; scrollToBottom(); loadHistory()
            break
          case 'cancelled':
          case 'stream_end':
            streaming.value = false
            if (taskStatus.value !== 'failed') taskStatus.value = taskStatus.value || 'completed'
            loadHistory()
            break
        }
      }

      // replay API 事件 → trace step（任务历史回放用）
      const replayEventToStep = (e) => pushStep(e.event_type, e.event_data || {})

      const onSSEError = (e) => {
        streaming.value = false
        state.notify('SSE 连接失败：' + (e.message || '未知错误'), 'error')
      }

      const onSSEClose = () => {
        // SSE 连接关闭：若任务未达终态，提示用户连接中断
        const terminal = ['completed', 'failed', 'cancelled'].includes(taskStatus.value)
        streaming.value = false
        if (!terminal && currentTaskId.value) {
          state.notify('实时连接已断开，可在历史中查看任务最终结果', 'warning')
          loadHistory()  // 刷新历史以获取最终状态
        }
      }

      const handleConfirm = async (decision) => {
        try {
          await window.API.post('/agent/tasks/' + currentTaskId.value + '/confirm', { decision })
          waitingConfirm.value = false
          state.notify('已' + (decision === 'approve' ? '同意' : '拒绝') + '确认', 'success')
        } catch (e) {
          state.notify('确认操作失败：' + (e.message || '未知错误'), 'error')
        }
      }

      const cancelTask = async () => {
        if (!currentTaskId.value) return
        try {
          if (abortSSE) { abortSSE(); abortSSE = null }
          await window.API.post('/agent/tasks/' + currentTaskId.value + '/cancel')
          streaming.value = false
          taskStatus.value = 'cancelled'
          state.notify('任务已取消', 'info')
          loadHistory()
        } catch (e) {
          state.notify('取消任务失败：' + (e.message || '未知错误'), 'error')
        }
      }

      // Phase 3: 任务完成后快捷动作
      const showPostActions = ref(false)
      const saveAsSkill = async () => {
        if (!currentTaskId.value) return
        const saved = await window.SkillActions.extractAndSave(currentTaskId.value, state)
        if (saved) showPostActions.value = false
      }
      const rerunTask = () => {
        const lastMsg = messages.filter(m => m.role === 'user').pop()
        if (lastMsg) inputMsg.value = lastMsg.content; showPostActions.value = false
      }

      // 任务历史回放：调 replay API 加载完整事件流 → 渲染轨迹
      const viewHistory = async (row) => {
        currentTaskId.value = row.task_id
        taskStatus.value = row.status
        messages.length = 0
        taskSteps.length = 0
        streamingText.value = ''
        try {
          const res = await window.API.get('/agent/tasks/' + row.task_id + '/replay')
          const task = res.task || {}
          if (task.original_message) {
            messages.push({ role: 'user', content: task.original_message, time: (task.created_at || '').slice(11, 19) })
          }
          const answer = (task.result && (task.result.content || task.result.answer)) || ''
          if (answer) {
            messages.push({ role: 'assistant', content: answer, time: (task.finished_at || '').slice(11, 19) })
          }
          ;(res.events || []).forEach(replayEventToStep)
        } catch (e) {
          // 降级：只显示最终结果
          if (row.result && (row.result.content || row.result.answer)) {
            messages.push({ role: 'assistant', content: row.result.content || row.result.answer, time: row.created_at })
          }
        }
      }

      onMounted(async () => {
        await loadHistory()
        if (state.pendingMessage) {
          inputMsg.value = state.pendingMessage; state.pendingMessage = ''
          nextTick(() => sendMessage())
        }
        // Phase 3: Command Palette 联动 — 保存最近任务为 Skill
        if (state.pendingAction === 'save-last-task-as-skill') {
          state.pendingAction = ''
          if (history.value.length > 0) {
            currentTaskId.value = history.value[0].task_id
            nextTick(() => saveAsSkill())
          }
        }
      })

      return {
        messages, inputMsg, currentTaskId, taskStatus, taskSteps, waitingConfirm,
        history, streaming, streamingText, canSend, chatBox, columns, renderMarkdown,
        sendMessage, handleConfirm, cancelTask, viewHistory, state,
        showPostActions, saveAsSkill, rerunTask
      }
    },
    template: `
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <!-- 对话区 -->
        <div class="lg:col-span-2 space-y-4">
          <base-card title="Agent 对话" :subtitle="taskStatus ? '当前状态：' + taskStatus : '发起对话，Agent 自主完成'">
            <template #action>
              <button v-if="streaming" class="btn btn-danger text-sm" @click="cancelTask">取消任务</button>
            </template>

            <!-- 消息列表 -->
            <div ref="chatBox" class="h-[400px] overflow-y-auto space-y-3 mb-4 p-3 bg-surface-muted rounded-md" role="log" aria-label="对话历史">
              <empty-state v-if="messages.length === 0" icon="💬" title="开始对话" description="输入消息，Agent 将自主调用工具完成任务" />
              <div v-for="(m, i) in messages" :key="i"
                   :class="['flex', m.role === 'user' ? 'justify-end' : 'justify-start']">
                <div :class="['max-w-[80%] px-4 py-2 rounded-lg text-sm',
                              m.role === 'user' ? 'bg-brand text-white' : 'bg-surface border border-border text-ink']">
                  <div v-if="m.role === 'user'" class="whitespace-pre-wrap">{{ m.content }}</div>
                  <div v-else class="markdown-body" v-html="renderMarkdown(m.content)"></div>
                  <p :class="['text-xs mt-1', m.role === 'user' ? 'text-blue-100' : 'text-ink-subtle']">{{ m.time }}</p>
                </div>
              </div>
              <div v-if="streaming" class="flex justify-start">
                <div class="bg-surface border border-border px-4 py-2 rounded-lg text-sm text-ink-muted">
                  <span class="inline-block animate-pulse">●●● 思考中</span>
                </div>
              </div>
            </div>

            <!-- 执行轨迹（结构化时间线，消除黑盒） -->
            <div data-tour="agent-trace">
              <agent-trace :steps="taskSteps" :streaming-text="streamingText" :streaming="streaming" />
            </div>

            <!-- Phase 3: 任务完成后快捷动作条 -->
            <div v-if="showPostActions && taskStatus === 'completed'" class="flex gap-2 mb-3">
              <button class="btn btn-secondary text-sm" @click="saveAsSkill">💾 保存为 Skill</button>
              <button class="btn btn-ghost text-sm" @click="rerunTask">🔄 重新发起</button>
              <button class="btn btn-ghost text-sm" @click="showPostActions = false">关闭</button>
            </div>

            <!-- HITL 确认 -->
            <div v-if="waitingConfirm" class="card p-3 mb-3 bg-amber-50 border-amber-200">
              <p class="text-sm text-amber-900 mb-2">⚠️ Agent 需要您确认后才能继续</p>
              <div class="flex gap-2">
                <button class="btn btn-primary text-sm" @click="handleConfirm('approve')">同意执行</button>
                <button class="btn btn-danger text-sm" @click="handleConfirm('reject')">拒绝</button>
              </div>
            </div>

            <!-- 输入框 -->
            <form @submit.prevent="sendMessage" class="flex gap-2">
              <label for="agent-input" class="sr-only">输入消息</label>
              <input id="agent-input" type="text" v-model="inputMsg"
                     class="input flex-1" placeholder="输入消息，回车发送..."
                     :disabled="streaming" :aria-busy="streaming" />
              <button type="submit" class="btn btn-primary" :disabled="!canSend" :aria-busy="streaming">
                {{ streaming ? '执行中...' : '发送' }}
              </button>
            </form>
          </base-card>
        </div>

        <!-- 任务历史 -->
        <div>
          <base-card title="任务历史">
            <button class="btn btn-ghost text-sm mb-3" @click="loadHistory">⟳ 刷新</button>
            <base-table :columns="columns" :rows="history" empty="暂无历史">
              <template #task_id="{ value }">
                <button class="font-mono text-xs text-brand hover:underline" @click="viewHistory(history.find(h => h.task_id === value))">
                  {{ value?.slice(0, 8) }}...
                </button>
              </template>
              <template #status="{ value }"><status-badge :status="value" /></template>
              <template #created_at="{ value }"><span class="text-xs text-ink-muted">{{ value?.slice(0, 16) }}</span></template>
            </base-table>
          </base-card>
        </div>
      </div>
    `
  }
})()
