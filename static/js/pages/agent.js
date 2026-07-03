/* Agent 任务页 — 对话界面 + 任务历史 + SSE 实时订阅 */
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
      const chatBox = ref(null)

      const columns = [
        { key: 'task_id', label: '任务ID', width: '120px' },
        { key: 'status', label: '状态', width: '100px' },
        { key: 'created_at', label: '时间', width: '150px' }
      ]

      const canSend = computed(() => inputMsg.value.trim() && !streaming.value)

      const loadHistory = async () => {
        try {
          const res = await window.API.get('/agent/tasks', { page: 1, page_size: 20 })
          history.value = res.items || []
        } catch (e) {}
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
        taskStatus.value = 'pending'
        taskSteps.length = 0

        try {
          const data = await window.API.post('/agent/chat', {
            message: msg, channel: 'api', stream: false
          })
          currentTaskId.value = data.task_id
          state.notify('Agent 任务已创建：' + data.task_id.slice(0, 8), 'info')
          // 轮询任务状态（简化版，SSE 可后续接入）
          pollTaskStatus(data.task_id)
        } catch (e) {
          streaming.value = false
        }
      }

      const pollTaskStatus = async (taskId) => {
        const maxRounds = 60
        for (let i = 0; i < maxRounds; i++) {
          try {
            const t = await window.API.get('/agent/tasks/' + taskId)
            taskStatus.value = t.status
            if (t.steps?.length) {
              taskSteps.length = 0
              taskSteps.push(...t.steps)
            }
            if (['completed', 'failed', 'cancelled'].includes(t.status)) {
              streaming.value = false
              if (t.result?.content) {
                messages.push({ role: 'assistant', content: t.result.content, time: new Date().toLocaleTimeString() })
              } else if (t.status === 'failed' && t.error) {
                messages.push({ role: 'assistant', content: '⚠️ 任务失败：' + (t.error.message || '未知错误'), time: new Date().toLocaleTimeString() })
              }
              scrollToBottom()
              loadHistory()
              return
            }
            if (t.status === 'waiting_confirm') waitingConfirm.value = true
            await new Promise(r => setTimeout(r, 2000))
          } catch (e) {
            streaming.value = false
            return
          }
        }
        streaming.value = false
        state.notify('任务超时，请稍后查看结果', 'warning')
      }

      const handleConfirm = async (decision) => {
        try {
          await window.API.post('/agent/tasks/' + currentTaskId.value + '/confirm', { decision })
          waitingConfirm.value = false
          state.notify('已' + (decision === 'approve' ? '同意' : '拒绝') + '确认', 'success')
        } catch (e) {}
      }

      const cancelTask = async () => {
        if (!currentTaskId.value) return
        try {
          await window.API.post('/agent/tasks/' + currentTaskId.value + '/cancel')
          streaming.value = false
          state.notify('任务已取消', 'info')
          loadHistory()
        } catch (e) {}
      }

      const viewHistory = (row) => {
        currentTaskId.value = row.task_id
        taskStatus.value = row.status
        messages.length = 0
        if (row.result?.content) messages.push({ role: 'assistant', content: row.result.content, time: row.created_at })
      }

      onMounted(loadHistory)

      return {
        messages, inputMsg, currentTaskId, taskStatus, taskSteps, waitingConfirm,
        history, streaming, canSend, chatBox, columns,
        sendMessage, handleConfirm, cancelTask, viewHistory, state
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
                  <p class="whitespace-pre-wrap">{{ m.content }}</p>
                  <p :class="['text-xs mt-1', m.role === 'user' ? 'text-blue-100' : 'text-ink-subtle']">{{ m.time }}</p>
                </div>
              </div>
              <div v-if="streaming" class="flex justify-start">
                <div class="bg-surface border border-border px-4 py-2 rounded-lg text-sm text-ink-muted">
                  <span class="inline-block animate-pulse">●●● 思考中</span>
                </div>
              </div>
            </div>

            <!-- 执行步骤 -->
            <details v-if="taskSteps.length > 0" class="mb-3">
              <summary class="cursor-pointer text-sm text-ink-muted">执行步骤 ({{ taskSteps.length }})</summary>
              <ol class="mt-2 space-y-1 text-xs text-ink-muted pl-4">
                <li v-for="(s, i) in taskSteps" :key="i" class="list-decimal">
                  <span class="font-medium">{{ s.action || s.type }}</span>: {{ s.summary || s.detail || '' }}
                </li>
              </ol>
            </details>

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
