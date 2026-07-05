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
      const form = reactive({ name: '', description: '', definition: '{"nodes":[],"edges":[],"variables":[]}', enabled: true })
      const editorMode = ref('visual')  // visual | json
      const wfContainer = ref(null)
      const wfPalette = ref(null)

      const showRun = ref(false)
      const runWorkflow = ref(null)
      const runInputs = ref('{}')
      const runResult = ref(null)

      const isAdmin = computed(() => state.hasRole('admin'))

      const columns = computed(() => {
        const cols = [
          { key: 'name', label: '名称' },
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
        } finally { loading.value = false }
      }

      // Phase C2: 初始化 Drawflow 编辑器（modal 打开后 nextTick 拿 DOM）
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
        Object.assign(form, { name: '', description: '', definition: '{"nodes":[],"edges":[],"variables":[]}', enabled: true })
        editingId.value = ''
        editorMode.value = 'visual'
        showForm.value = true
        initEditor({ nodes: [], edges: [], variables: [] })
      }

      const openEdit = (row) => {
        const def = row.definition || { nodes: [], edges: [], variables: [] }
        Object.assign(form, {
          name: row.name, description: row.description || '',
          definition: JSON.stringify(def, null, 2), enabled: row.enabled
        })
        editingId.value = row.id
        editorMode.value = 'visual'
        showForm.value = true
        initEditor(def)
      }

      // modal 关闭时销毁编辑器
      watch(showForm, (v) => { if (!v && window.WorkflowEditor) window.WorkflowEditor.destroy() })

      const submitForm = async () => {
        try {
          // visual 模式从 editor 导出最新 JSON；json 模式从 textarea 解析
          let def = {}
          if (editorMode.value === 'visual' && window.WorkflowEditor) {
            def = window.WorkflowEditor.exportJSON()
          } else {
            try { def = JSON.parse(form.definition) } catch (e) {
              state.notify('definition 不是合法 JSON', 'error'); return
            }
          }
          const payload = { name: form.name, description: form.description, definition: def, enabled: form.enabled }
          if (editingId.value) {
            await window.API.put('/workflows/' + editingId.value, payload)
            state.notify('工作流更新成功', 'success')
          } else {
            await window.API.post('/workflows', payload)
            state.notify('工作流创建成功', 'success')
          }
          showForm.value = false
          loadList()
        } catch (e) {}
      }

      const removeRow = async (row) => {
        if (!confirm('确认删除工作流 "' + row.name + '"？')) return
        try { await window.API.del('/workflows/' + row.id); state.notify('已删除', 'success'); loadList() } catch (e) {}
      }

      const toggleEnabled = async (row) => {
        try {
          await window.API.put('/workflows/' + row.id, { enabled: !row.enabled })
          state.notify(row.enabled ? '已禁用' : '已启用', 'success')
          loadList()
        } catch (e) {}
      }

      const openRun = (row) => {
        runWorkflow.value = row
        runInputs.value = '{}'
        runResult.value = null
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
          runResult.value = { execution_id: res.execution_id, status: res.status, message: '执行已启动，可稍后查看状态' }
          state.notify('工作流已触发', 'success')
        } catch (e) {}
      }

      const onPageChange = (p) => { page.value = p; loadList() }
      const onSearch = () => { page.value = 1; loadList() }

      onMounted(loadList)

      return {
        list, total, page, pageSize, keyword, loading, columns,
        showForm, editingId, form, isAdmin, editorMode, wfContainer, wfPalette,
        showRun, runWorkflow, runInputs, runResult,
        loadList, openCreate, openEdit, submitForm, removeRow, toggleEnabled,
        openRun, executeRun, onPageChange, onSearch, state
      }
    },
    template: `
      <div class="space-y-4">
        <base-card>
          <div class="flex flex-wrap gap-3 items-center">
            <input v-model="keyword" type="text" class="input flex-1 min-w-[200px]" placeholder="搜索工作流名称" @keydown.enter="onSearch" aria-label="搜索" />
            <button class="btn btn-secondary" @click="onSearch">搜索</button>
            <button v-if="isAdmin" class="btn btn-primary ml-auto" @click="openCreate">+ 新建工作流</button>
          </div>
        </base-card>

        <base-card>
          <base-table :columns="columns" :rows="list" :loading="loading">
            <template #name="{ row }">
              <p class="font-medium text-ink">{{ row.name }}</p>
              <p class="text-xs text-ink-muted">{{ row.description || '无描述' }}</p>
            </template>
            <template #enabled="{ value }">
              <span :class="['badge', value ? 'badge-success' : 'badge-muted']">{{ value ? '启用' : '禁用' }}</span>
            </template>
            <template #version="{ value }"><span class="text-sm">v{{ value }}</span></template>
            <template #created_at="{ value }"><span class="text-xs text-ink-muted">{{ value?.slice(0, 16) }}</span></template>
            <template #actions="{ row }">
              <div class="flex gap-1 justify-center">
                <button class="btn btn-ghost text-xs" @click="openRun(row)" title="执行">▶️</button>
                <button v-if="isAdmin" class="btn btn-ghost text-xs" @click="openEdit(row)" title="编辑">✏️</button>
                <button v-if="isAdmin" class="btn btn-ghost text-xs" @click="toggleEnabled(row)" :title="row.enabled ? '禁用' : '启用'">{{ row.enabled ? '⏸️' : '▶️' }}</button>
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
              <p class="text-sm text-blue-900"><strong>执行 ID：</strong>{{ runResult.execution_id }}</p>
              <p class="text-sm text-blue-900"><strong>状态：</strong>{{ runResult.status }}</p>
              <p class="text-xs text-blue-700 mt-1">{{ runResult.message }}</p>
            </div>
          </div>
          <template #footer>
            <button class="btn btn-secondary" @click="showRun = false">关闭</button>
            <button class="btn btn-primary" @click="executeRun">▶ 执行</button>
          </template>
        </base-modal>
      </div>
    `
  }
})()
