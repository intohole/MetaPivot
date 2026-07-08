/* 工作流管理 — 列表 + 创建/编辑(可视化 Drawflow + JSON 双模式) + 执行 + 历史 */
(function () {
  const { ref, reactive, onMounted, computed, watch, nextTick } = Vue
  window.Pages = window.Pages || {}

  window.Pages.Workflows = {
    name: 'WorkflowsPage',
    setup() {
      const state = window.AppState
      const list = ref([])
      const total = ref(0)
      const page = ref(1)
      const pageSize = ref(20)
      const keyword = ref('')
      const loading = ref(false)

      const showForm = ref(false)
      const editingId = ref('')
      const form = reactive({ name: '', description: '', definition: '{"nodes":[],"edges":[],"variables":[]}', enabled: true, trigger: { type: 'manual', cron_expr: '', im_keyword: '', im_chat_filter: '' } })
      const editorMode = ref('visual')  // visual | json
      const wfContainer = ref(null)
      const wfPalette = ref(null)

      const showRun = ref(false)
      const runWorkflow = ref(null)
      const runInputs = ref('{}')
      const runResult = ref(null)

      // Sprint 4: 复制为 Skill 主题化 Modal（替代 window.prompt）
      const showSaveSkill = ref(false)
      const saveSkillForm = reactive({ name: '', description: '', tags: [] })
      const savingSkill = ref(false)

      const isAdmin = computed(() => state.hasRole('admin'))

      const columns = computed(() => {
        const cols = [
          { key: 'name', label: '名称' },
          { key: 'trigger', label: '触发', width: '90px' },
          { key: 'enabled', label: '状态', width: '80px' },
          { key: 'version', label: '版本', width: '80px' },
          { key: 'created_at', label: '创建时间', width: '160px' }
        ]
        cols.push({ key: 'actions', label: '操作', width: '220px', align: 'center' })
        return cols
      })

      const loadList = async () => {
        loading.value = true
        try {
          const res = await window.API.get('/workflows', { page: page.value, page_size: pageSize.value, keyword: keyword.value })
          list.value = res.items || []
          total.value = res.total || 0
        } catch (e) {
          state.notify('加载工作流列表失败：' + (e.message || '未知错误'), 'error')
        } finally { loading.value = false }
      }

      const initEditor = (definition) => {
        nextTick(() => {
          if (!wfContainer.value || !window.WorkflowEditor) return
          if (window.WorkflowEditor.destroy) window.WorkflowEditor.destroy()
          window.WorkflowEditor.init(wfContainer.value, (json) => {
            form.definition = JSON.stringify(json, null, 2)  // 画布变化同步到 JSON
          })
          if (wfPalette.value) window.WorkflowEditor.renderPalette(wfPalette.value)
          window.WorkflowEditor.loadJSON(definition || { nodes: [], edges: [], variables: [] })
        })
      }

      const openCreate = () => {
        Object.assign(form, { name: '', description: '', definition: '{"nodes":[],"edges":[],"variables":[]}', enabled: true, trigger: { type: 'manual', cron_expr: '', im_keyword: '', im_chat_filter: '' } })
        editingId.value = ''
        editorMode.value = 'visual'
        showForm.value = true
        initEditor({ nodes: [], edges: [], variables: [] })
      }

      const openEdit = (row) => {
        const def = row.definition || { nodes: [], edges: [], variables: [] }
        const trig = row.trigger || { type: 'manual', cron_expr: '', im_keyword: '', im_chat_filter: '' }
        Object.assign(form, {
          name: row.name, description: row.description || '',
          definition: JSON.stringify(def, null, 2), enabled: row.enabled,
          trigger: { type: trig.type || 'manual', cron_expr: trig.cron_expr || '', im_keyword: trig.im_keyword || '', im_chat_filter: trig.im_chat_filter || '' }
        })
        editingId.value = row.id
        editorMode.value = 'visual'
        showForm.value = true
        initEditor(def)
      }

      watch(showForm, (v) => { if (!v && window.WorkflowEditor) window.WorkflowEditor.destroy() })

      const submitForm = async () => {
        try {
          let def = {}
          if (editorMode.value === 'visual' && window.WorkflowEditor) {
            def = window.WorkflowEditor.exportJSON()
          } else {
            try { def = JSON.parse(form.definition) } catch (e) {
              state.notify('definition 不是合法 JSON', 'error'); return
            }
          }
          const payload = { name: form.name, description: form.description, definition: def, enabled: form.enabled, trigger: form.trigger }
          if (editingId.value) {
            await window.API.put('/workflows/' + editingId.value, payload)
            state.notify('工作流更新成功', 'success')
          } else {
            await window.API.post('/workflows', payload)
            state.notify('工作流创建成功', 'success')
          }
          showForm.value = false
          loadList()
        } catch (e) {
          state.notify('操作失败：' + (e.message || '未知错误'), 'error')
        }
      }

      const removeRow = async (row) => {
        const action = await state.confirmAction({
          title: '确认删除', message: '确认删除工作流 "' + row.name + '"？此操作不可撤销。',
          confirmText: '删除', danger: true
        })
        if (action !== 'confirm') return
        try { await window.API.del('/workflows/' + row.id); state.notify('已删除', 'success'); loadList() } catch (e) { state.notify('删除失败：' + (e.message || '未知错误'), 'error') }
      }

      const toggleEnabled = async (row) => {
        try {
          await window.API.put('/workflows/' + row.id, { enabled: !row.enabled })
          state.notify(row.enabled ? '已禁用' : '已启用', 'success')
          loadList()
        } catch (e) {
          state.notify('操作失败：' + (e.message || '未知错误'), 'error')
        }
      }

      const openRun = (row) => {
        runWorkflow.value = row
        runInputs.value = '{}'; runResult.value = null
        showRun.value = true
      }

      const executeRun = async () => {
        if (!runWorkflow.value) return
        let inputs = {}
        try { inputs = JSON.parse(runInputs.value) } catch (e) {
          state.notify('输入不是合法 JSON', 'error'); return
        }
        try {
          const res = await window.API.post('/workflows/' + runWorkflow.value.id + '/execute', { inputs })
          runResult.value = { execution_id: res.execution_id, status: res.status, message: '执行已启动，可点击下方按钮刷新状态' }
          state.notify('工作流已触发', 'success')
        } catch (e) {
          state.notify('操作失败：' + (e.message || '未知错误'), 'error')
        }
      }

      const checkRunStatus = async () => {
        if (!runResult.value?.execution_id) return
        try {
          const res = await window.API.get('/workflows/executions/' + runResult.value.execution_id)
          runResult.value = { ...runResult.value, status: res.status, current_node: res.current_node, outputs: res.outputs, error: res.error }
        } catch (e) {
          state.notify('操作失败：' + (e.message || '未知错误'), 'error')
        }
      }

      // Sprint 4: 复制工作流为 Skill（主题化 Modal 替代 window.prompt）
      const openSaveSkill = () => {
        if (!runWorkflow.value) return
        saveSkillForm.name = runWorkflow.value.name + ' (Skill)'
        saveSkillForm.description = runWorkflow.value.description || '从工作流创建'
        saveSkillForm.tags = []
        showSaveSkill.value = true
      }
      const confirmSaveSkill = async () => {
        if (!saveSkillForm.name.trim()) { state.notify('请填写 Skill 名称', 'error'); return }
        if (!runWorkflow.value) return
        savingSkill.value = true
        try {
          const saved = await window.SkillActions.saveFromWorkflow(runWorkflow.value.id, {
            name: saveSkillForm.name.trim(),
            description: saveSkillForm.description,
            tags: saveSkillForm.tags,
          })
          state.notify('Skill 创建成功：' + (saved.name || saveSkillForm.name), 'success')
          showSaveSkill.value = false
        } catch (e) {
          state.notify('创建失败：' + (e.message || '未知错误'), 'error')
        } finally { savingSkill.value = false }
      }

      const onPageChange = ({ page: p, pageSize: ps }) => { page.value = p; if (ps) pageSize.value = ps; loadList() }
      const onSearch = () => { page.value = 1; loadList() }
      const goTemplates = () => state.navigate('/templates')

      onMounted(() => {
        loadList()
        if (state.pendingAction === 'create-workflow') {
          state.pendingAction = ''
          nextTick(() => openCreate())
        }
      })

      return {
        list, total, page, pageSize, keyword, loading, columns,
        showForm, editingId, form, isAdmin, editorMode, wfContainer, wfPalette,
        showRun, runWorkflow, runInputs, runResult,
        showSaveSkill, saveSkillForm, savingSkill,
        loadList, openCreate, openEdit, submitForm, removeRow, toggleEnabled,
        openRun, executeRun, checkRunStatus, openSaveSkill, confirmSaveSkill,
        onPageChange, onSearch, goTemplates, state,
      }
    },
    template: `
      <div class="space-y-4">
        <base-card>
          <div class="flex flex-wrap gap-3 items-center">
            <input v-model="keyword" type="text" class="input flex-1 min-w-[200px]" placeholder="搜索工作流名称" @keydown.enter="onSearch" aria-label="搜索" />
            <button class="btn btn-secondary" @click="onSearch">搜索</button>
            <div class="ml-auto flex gap-2">
              <button class="btn btn-secondary" @click="goTemplates" title="从 SOP 模板一键创建">🗂️ 从模板创建</button>
              <button v-if="isAdmin" class="btn btn-primary" @click="openCreate">+ 新建工作流</button>
            </div>
          </div>
        </base-card>

        <base-card>
          <base-table :columns="columns" :rows="list" :loading="loading">
            <template #name="{ row }">
              <p class="font-medium text-ink">{{ row.name }}</p>
              <p class="text-xs text-ink-muted">{{ row.description || '无描述' }}</p>
            </template>
            <template #trigger="{ row }">
              <span :class="['badge', row.trigger?.type === 'webhook' ? 'badge-info' : row.trigger?.type === 'schedule' ? 'badge-warning' : row.trigger?.type === 'im_message' ? 'badge-success' : 'badge-muted']" :title="row.trigger?.im_keyword ? ('关键词: ' + row.trigger.im_keyword) : ''">{{ {manual:'手动', webhook:'Webhook', schedule:'定时', im_message:'IM消息'}[row.trigger?.type] || '手动' }}</span>
            </template>
            <template #enabled="{ row }">
              <switch v-if="isAdmin" :model-value="row.enabled" @update:model-value="() => toggleEnabled(row)" :aria-label="(row.enabled ? '禁用' : '启用') + ' ' + row.name" size="sm" />
              <span v-else :class="['badge', row.enabled ? 'badge-success' : 'badge-muted']">{{ row.enabled ? '启用' : '禁用' }}</span>
            </template>
            <template #version="{ value }"><span class="text-sm">v{{ value }}</span></template>
            <template #created_at="{ value }"><span class="text-xs text-ink-muted">{{ value?.slice(0, 16) }}</span></template>
            <template #actions="{ row }">
              <div class="flex gap-1 justify-center">
                <button class="btn btn-ghost text-xs" @click="openRun(row)" title="执行">▶️</button>
                <button v-if="isAdmin" class="btn btn-ghost text-xs" @click="openEdit(row)" title="编辑">✏️</button>
                <button v-if="isAdmin" class="btn btn-ghost text-xs text-danger" @click="removeRow(row)" title="删除">🗑️</button>
              </div>
            </template>
          </base-table>
          <pagination :page="page" :page-size="pageSize" :total="total" @change="onPageChange" />
        </base-card>

        <!-- 创建/编辑（可视化 Drawflow + JSON 双模式） -->
        <base-modal v-model="showForm" :title="editingId ? '编辑工作流' : '新建工作流'" width="max-w-5xl">
          <div class="space-y-4">
            <div class="grid grid-cols-2 gap-4">
              <div>
                <label for="wf-name" class="block text-sm font-medium text-ink mb-1">名称 *</label>
                <input id="wf-name" type="text" v-model="form.name" class="input" placeholder="工作流名称" />
              </div>
              <div>
                <label class="block text-sm font-medium text-ink mb-1">启用状态</label>
                <label class="flex items-center gap-2 h-[38px]">
                  <input type="checkbox" v-model="form.enabled" />
                  <span class="text-sm text-ink">启用此工作流</span>
                </label>
              </div>
            </div>
            <div>
              <label for="wf-desc" class="block text-sm font-medium text-ink mb-1">描述</label>
              <textarea id="wf-desc" v-model="form.description" class="textarea" rows="2"></textarea>
            </div>
            <div>
              <label class="block text-sm font-medium text-ink mb-1">触发方式</label>
              <div class="flex flex-wrap gap-3 items-center">
                <label class="flex items-center gap-1">
                  <input type="radio" v-model="form.trigger.type" value="manual" />
                  <span class="text-sm">手动</span>
                </label>
                <label class="flex items-center gap-1">
                  <input type="radio" v-model="form.trigger.type" value="webhook" />
                  <span class="text-sm">Webhook</span>
                </label>
                <label class="flex items-center gap-1">
                  <input type="radio" v-model="form.trigger.type" value="schedule" />
                  <span class="text-sm">定时</span>
                </label>
                <label class="flex items-center gap-1">
                  <input type="radio" v-model="form.trigger.type" value="im_message" />
                  <span class="text-sm">IM消息</span>
                </label>
                <input v-if="form.trigger.type === 'schedule'" type="text" v-model="form.trigger.cron_expr" class="input flex-1 min-w-[180px] font-mono text-xs" placeholder="*/5 * * * *（分 时 日 月 周）" />
              </div>
              <p v-if="form.trigger.type === 'webhook'" class="mt-1 text-xs text-ink-subtle">保存后自动生成 Webhook URL（在 Webhook 管理页查看）</p>
              <div v-if="form.trigger.type === 'im_message'" class="mt-2 space-y-2 p-3 bg-surface-muted rounded">
                <div>
                  <label for="im-keyword" class="block text-xs font-medium text-ink mb-1">触发关键词 *</label>
                  <input id="im-keyword" type="text" v-model="form.trigger.im_keyword" class="input" placeholder="如：周报、站会、请假（IM 消息包含此词则触发）" />
                  <p class="mt-1 text-xs text-ink-subtle">大小写不敏感；消息文本包含此关键词即触发工作流</p>
                </div>
                <div>
                  <label for="im-chat-filter" class="block text-xs font-medium text-ink mb-1">限定会话 ID（可选）</label>
                  <input id="im-chat-filter" type="text" v-model="form.trigger.im_chat_filter" class="input" placeholder="留空表示所有会话都触发" />
                </div>
              </div>
            </div>
            <div>
              <div class="flex items-center justify-between mb-2">
                <label class="block text-sm font-medium text-ink">DAG 定义</label>
                <div class="flex gap-1">
                  <button type="button" :class="['btn text-xs', editorMode==='visual'?'btn-primary':'btn-secondary']" @click="editorMode='visual'">🎨 可视化</button>
                  <button type="button" :class="['btn text-xs', editorMode==='json'?'btn-primary':'btn-secondary']" @click="editorMode='json'">{ } JSON</button>
                </div>
              </div>
              <!-- 可视化模式：左侧节点面板 + 右侧 Drawflow 画布 -->
              <div v-if="editorMode==='visual'" class="flex gap-2">
                <div ref="wfPalette" class="w-36 bg-surface-muted p-2 rounded border border-border max-h-[420px] overflow-y-auto space-y-1" aria-label="节点面板"></div>
                <div ref="wfContainer" class="flex-1 h-[420px] border border-border rounded overflow-hidden bg-surface" aria-label="DAG 画布"></div>
              </div>
              <!-- JSON 模式：textarea -->
              <textarea v-else v-model="form.definition" class="textarea font-mono text-xs" rows="14" aria-label="DAG JSON"></textarea>
              <p class="mt-1 text-xs text-ink-subtle">节点类型：start/end/skill_call/llm_call/condition/send_message/hitl/parallel/agent_call/sub_workflow</p>
            </div>
          </div>
          <template #footer>
            <button class="btn btn-secondary" @click="showForm = false">取消</button>
            <button class="btn btn-primary" @click="submitForm">{{ editingId ? '保存' : '创建' }}</button>
          </template>
        </base-modal>

        <!-- 执行 -->
        <base-modal v-model="showRun" :title="'执行工作流：' + (runWorkflow?.name || '')" width="max-w-2xl">
          <div class="space-y-3">
            <div>
              <label for="run-input" class="block text-sm font-medium text-ink mb-1">输入参数 (JSON)</label>
              <textarea id="run-input" v-model="runInputs" class="textarea font-mono text-xs" rows="6"></textarea>
            </div>
            <div v-if="runResult" class="card p-3 bg-blue-50 border-blue-200">
              <div class="flex items-center justify-between mb-1">
                <p class="text-sm text-blue-900"><strong>执行 ID：</strong>{{ runResult.execution_id }}</p>
                <div class="flex gap-2">
                  <button class="btn btn-secondary text-xs" @click="checkRunStatus">🔄 刷新状态</button>
                  <button v-if="runWorkflow" class="btn btn-secondary text-xs" @click="openSaveSkill">📋 复制为 Skill</button>
                </div>
              </div>
              <p class="text-sm text-blue-900"><strong>状态：</strong>{{ runResult.status }}<span v-if="runResult.current_node"> | 当前节点：{{ runResult.current_node }}</span></p>
              <p class="text-xs text-blue-700 mt-1">{{ runResult.message || (runResult.error ? JSON.stringify(runResult.error) : '点击刷新查看最新状态') }}</p>
            </div>
          </div>
          <template #footer>
            <button class="btn btn-secondary" @click="showRun = false">关闭</button>
            <button class="btn btn-primary" @click="executeRun">▶ 执行</button>
          </template>
        </base-modal>

        <!-- Sprint 4: 复制为 Skill 主题化 Modal -->
        <base-modal v-model="showSaveSkill" title="复制为 Skill" width="max-w-lg">
          <div class="space-y-4">
            <div class="card p-3 bg-blue-50 border-blue-200">
              <p class="text-sm text-blue-900"><strong>源工作流：</strong>{{ runWorkflow?.name }}</p>
              <p class="text-xs text-blue-700 mt-1">将工作流封装为可复用 Skill，Agent 可按需调用</p>
            </div>
            <div>
              <label for="wf-skill-name" class="block text-sm font-medium text-ink mb-1">Skill 名称 *</label>
              <input id="wf-skill-name" type="text" v-model="saveSkillForm.name" class="input" placeholder="Skill 名称" />
            </div>
            <div>
              <label for="wf-skill-desc" class="block text-sm font-medium text-ink mb-1">描述</label>
              <textarea id="wf-skill-desc" v-model="saveSkillForm.description" class="textarea" rows="2"></textarea>
            </div>
            <div>
              <label class="block text-sm font-medium text-ink mb-1">标签</label>
              <tag-input v-model="saveSkillForm.tags" placeholder="输入标签后回车" />
            </div>
          </div>
          <template #footer>
            <button class="btn btn-secondary" @click="showSaveSkill = false">取消</button>
            <button class="btn btn-primary" @click="confirmSaveSkill" :disabled="savingSkill">
              {{ savingSkill ? '创建中...' : '📋 创建 Skill' }}
            </button>
          </template>
        </base-modal>
      </div>
    `
  }
})()
