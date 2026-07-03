/* 工作流管理 — 列表 + 创建/编辑(JSON 编辑器) + 执行 + 执行历史 */
(function () {
  const { ref, reactive, onMounted, computed } = Vue
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
      const form = reactive({ name: '', description: '', definition: '{\n  "nodes": [],\n  "edges": [],\n  "variables": []\n}', enabled: true })

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

      const openCreate = () => {
        Object.assign(form, { name: '', description: '', definition: '{\n  "nodes": [],\n  "edges": [],\n  "variables": []\n}', enabled: true })
        editingId.value = ''
        showForm.value = true
      }

      const openEdit = (row) => {
        Object.assign(form, {
          name: row.name, description: row.description || '',
          definition: JSON.stringify(row.definition || {}, null, 2),
          enabled: row.enabled
        })
        editingId.value = row.id
        showForm.value = true
      }

      const submitForm = async () => {
        try {
          let def = {}
          try { def = JSON.parse(form.definition) } catch (e) {
            state.notify('definition 不是合法 JSON', 'error'); return
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
        showForm, editingId, form, isAdmin, showRun, runWorkflow, runInputs, runResult,
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

        <!-- 创建/编辑 -->
        <base-modal v-model="showForm" :title="editingId ? '编辑工作流' : '新建工作流'" width="max-w-3xl">
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
              <label for="wf-def" class="block text-sm font-medium text-ink mb-1">DAG 定义 (JSON)</label>
              <textarea id="wf-def" v-model="form.definition" class="textarea font-mono text-xs" rows="14"></textarea>
              <p class="mt-1 text-xs text-ink-subtle">格式：{ "nodes": [...], "edges": [...], "variables": [...] }</p>
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
