/* Skill 管理页 — 列表 + 创建/编辑/启用禁用/测试 */
(function () {
  const { ref, reactive, onMounted, computed, nextTick } = Vue
  window.Pages = window.Pages || {}

  window.Pages.Skills = {
    name: 'SkillsPage',
    setup() {
      const state = window.AppState
      const list = ref([])
      const total = ref(0)
      const page = ref(1)
      const pageSize = ref(20)
      const keyword = ref('')
      const sourceType = ref('')
      const scope = ref('all')
      const loading = ref(false)

      const showForm = ref(false)
      const editingId = ref('')
      const form = reactive({
        name: '', description: '', input_schema: '{}', source_type: 'mcp',
        source_ref: '', permission: 'user', require_confirm: false, tags: ''
      })
      const showTest = ref(false)
      const testingSkill = ref(null)
      const testInput = ref('{}')
      const testResult = ref(null)

      const isAdmin = computed(() => state.hasRole('admin'))

      const columns = computed(() => {
        const cols = [
          { key: 'name', label: '名称' },
          { key: 'source_type', label: '类型', width: '100px' },
          { key: 'visibility', label: '归属', width: '80px' },
          { key: 'source_ref', label: '引用', width: '200px' },
          { key: 'call_count', label: '调用次数', width: '100px' },
          { key: 'enabled', label: '状态', width: '100px' }
        ]
        if (isAdmin.value) cols.push({ key: 'actions', label: '操作', width: '220px', align: 'center' })
        return cols
      })

      const loadList = async () => {
        loading.value = true
        try {
          const res = await window.API.get('/skills', {
            page: page.value, page_size: pageSize.value,
            keyword: keyword.value, source_type: sourceType.value, scope: scope.value
          })
          list.value = res.items || []
          total.value = res.total || 0
        } finally {
          loading.value = false
        }
      }

      const openCreate = () => {
        Object.assign(form, { name: '', description: '', input_schema: '{}', source_type: 'mcp', source_ref: '', permission: 'user', require_confirm: false, tags: '' })
        editingId.value = ''
        showForm.value = true
      }

      const openEdit = (row) => {
        Object.assign(form, {
          name: row.name, description: row.description || '',
          input_schema: JSON.stringify(row.input_schema || {}, null, 2),
          source_type: row.source_type, source_ref: row.source_ref,
          permission: row.permission || 'user', require_confirm: row.require_confirm || false,
          tags: (row.tags || []).join(',')
        })
        editingId.value = row.id
        showForm.value = true
      }

      const submitForm = async () => {
        try {
          let schema = {}
          try { schema = JSON.parse(form.input_schema) } catch (e) {
            state.notify('input_schema 不是合法 JSON', 'error'); return
          }
          const payload = {
            name: form.name, description: form.description, input_schema: schema,
            source_type: form.source_type, source_ref: form.source_ref,
            permission: form.permission, require_confirm: form.require_confirm,
            tags: form.tags ? form.tags.split(',').map(s => s.trim()).filter(Boolean) : []
          }
          if (editingId.value) {
            await window.API.put('/skills/' + editingId.value, payload)
            state.notify('Skill 更新成功', 'success')
          } else {
            await window.API.post('/skills', payload)
            state.notify('Skill 创建成功', 'success')
          }
          showForm.value = false
          loadList()
        } catch (e) {}
      }

      const toggleEnable = async (row) => {
        try {
          await window.API.post('/skills/' + row.id + '/' + (row.enabled ? 'disable' : 'enable'))
          state.notify(row.enabled ? '已禁用' : '已启用', 'success')
          loadList()
        } catch (e) {}
      }

      const removeRow = async (row) => {
        const action = await state.confirmAction({
          title: '确认删除', message: '确认删除 Skill "' + row.name + '"？此操作不可撤销。',
          confirmText: '删除', danger: true
        })
        if (action !== 'confirm') return
        try {
          await window.API.del('/skills/' + row.id)
          state.notify('已删除', 'success')
          loadList()
        } catch (e) {}
      }

      // Phase 3: 发布到团队 + scope 切换
      const publishToTeam = async (row) => {
        try {
          await window.API.post('/skills/' + row.id + '/publish')
          state.notify('已发布到团队', 'success')
          loadList()
        } catch (e) {}
      }
      const onScopeChange = (s) => { scope.value = s; page.value = 1; loadList() }

      const openTest = (row) => {
        testingSkill.value = row
        testInput.value = JSON.stringify(row.input_schema || {}, null, 2)
        testResult.value = null
        showTest.value = true
      }

      const runTest = async () => {
        if (!testingSkill.value) return
        let input = {}
        try { input = JSON.parse(testInput.value) } catch (e) {
          state.notify('输入不是合法 JSON', 'error'); return
        }
        try {
          testResult.value = await window.API.post('/skills/' + testingSkill.value.id + '/test', { input })
          state.notify('测试执行完成', 'success')
        } catch (e) { testResult.value = { success: false, error: { message: '执行失败' } } }
      }

      const onPageChange = ({ page: p, pageSize: ps }) => { page.value = p; if (ps) pageSize.value = ps; loadList() }
      const onSearch = () => { page.value = 1; loadList() }

      onMounted(() => {
        loadList()
        if (state.pendingAction === 'create-skill') {
          state.pendingAction = ''
          nextTick(() => openCreate())
        }
      })

      return {
        list, total, page, pageSize, keyword, sourceType, scope, loading, columns,
        showForm, editingId, form, isAdmin, showTest, testingSkill, testInput, testResult,
        loadList, openCreate, openEdit, submitForm, toggleEnable, removeRow,
        openTest, runTest, onPageChange, onSearch, onScopeChange, publishToTeam, state
      }
    },
    template: `
      <div class="space-y-4">
        <!-- 筛选栏 -->
        <base-card>
          <div class="flex gap-2 mb-3">
            <button :class="['btn text-sm', scope==='all'?'btn-primary':'btn-ghost']" @click="onScopeChange('all')">全部</button>
            <button :class="['btn text-sm', scope==='my'?'btn-primary':'btn-ghost']" @click="onScopeChange('my')">我的</button>
            <button :class="['btn text-sm', scope==='team'?'btn-primary':'btn-ghost']" @click="onScopeChange('team')">团队</button>
          </div>
          <div class="flex flex-wrap gap-3 items-center">
            <input v-model="keyword" type="text" class="input flex-1 min-w-[200px]" placeholder="搜索 Skill 名称" @keydown.enter="onSearch" aria-label="搜索" />
            <select v-model="sourceType" class="select w-40" @change="onSearch" aria-label="类型筛选">
              <option value="">全部类型</option>
              <option value="mcp">MCP</option>
              <option value="function">Function</option>
              <option value="workflow">Workflow</option>
            </select>
            <button class="btn btn-secondary" @click="onSearch">搜索</button>
            <button v-if="isAdmin" class="btn btn-primary ml-auto" @click="openCreate">+ 新建 Skill</button>
          </div>
        </base-card>

        <!-- 列表 -->
        <base-card>
          <base-table :columns="columns" :rows="list" :loading="loading">
            <template #name="{ row }">
              <p class="font-medium text-ink">{{ row.name }}</p>
              <p class="text-xs text-ink-muted">{{ row.description }}</p>
            </template>
            <template #source_ref="{ value }"><span class="font-mono text-xs text-ink-muted">{{ value }}</span></template>
            <template #source_type="{ value }"><span class="badge badge-info">{{ value }}</span></template>
            <template #visibility="{ row }">
              <span :class="['badge', row.visibility==='shared'?'badge-success':'badge-muted']">{{ row.visibility==='shared'?'团队':'私有' }}</span>
            </template>
            <template #call_count="{ value }"><span class="text-sm">{{ value || 0 }}</span></template>
            <template #enabled="{ row }">
              <switch v-if="isAdmin" :model-value="row.enabled" @update:model-value="() => toggleEnable(row)" :aria-label="(row.enabled ? '禁用' : '启用') + ' ' + row.name" size="sm" />
              <span v-else :class="['badge', row.enabled ? 'badge-success' : 'badge-muted']">{{ row.enabled ? '启用' : '禁用' }}</span>
            </template>
            <template #actions="{ row }">
              <div class="flex gap-1 justify-center">
                <button class="btn btn-ghost text-xs" @click="openTest(row)" title="测试">🧪</button>
                <button v-if="isAdmin && row.visibility==='private' && (!row.owner_id || row.owner_id===state.user.value?.id)" class="btn btn-ghost text-xs" @click="publishToTeam(row)" title="发布到团队">🌐</button>
                <button v-if="isAdmin" class="btn btn-ghost text-xs" @click="openEdit(row)" title="编辑">✏️</button>
                <button v-if="isAdmin" class="btn btn-ghost text-xs text-danger" @click="removeRow(row)" title="删除">🗑️</button>
              </div>
            </template>
          </base-table>
          <pagination :page="page" :page-size="pageSize" :total="total" @change="onPageChange" />
        </base-card>

        <!-- 创建/编辑表单 -->
        <base-modal v-model="showForm" :title="editingId ? '编辑 Skill' : '新建 Skill'" width="max-w-2xl">
          <div class="space-y-4">
            <div>
              <label for="skill-name" class="block text-sm font-medium text-ink mb-1">名称 *</label>
              <input id="skill-name" type="text" v-model="form.name" class="input" placeholder="如：查询用户信息" />
            </div>
            <div>
              <label for="skill-desc" class="block text-sm font-medium text-ink mb-1">描述</label>
              <textarea id="skill-desc" v-model="form.description" class="textarea" rows="2" placeholder="Skill 用途说明"></textarea>
            </div>
            <div class="grid grid-cols-2 gap-4">
              <div>
                <label for="skill-type" class="block text-sm font-medium text-ink mb-1">来源类型</label>
                <select id="skill-type" v-model="form.source_type" class="select">
                  <option value="mcp">MCP 工具</option>
                  <option value="function">Function Call</option>
                  <option value="workflow">工作流</option>
                </select>
              </div>
              <div>
                <label for="skill-ref" class="block text-sm font-medium text-ink mb-1">来源引用 *</label>
                <input id="skill-ref" type="text" v-model="form.source_ref" class="input" placeholder="如：mcp.tool.name 或 function.py" />
              </div>
            </div>
            <div>
              <label for="skill-schema" class="block text-sm font-medium text-ink mb-1">输入 Schema (JSON)</label>
              <textarea id="skill-schema" v-model="form.input_schema" class="textarea font-mono text-xs" rows="6"></textarea>
            </div>
            <div class="grid grid-cols-2 gap-4">
              <div>
                <label for="skill-perm" class="block text-sm font-medium text-ink mb-1">权限</label>
                <select id="skill-perm" v-model="form.permission" class="select">
                  <option value="user">普通用户</option>
                  <option value="manager">管理者</option>
                  <option value="admin">仅管理员</option>
                </select>
              </div>
              <div>
                <label for="skill-tags" class="block text-sm font-medium text-ink mb-1">标签（逗号分隔）</label>
                <input id="skill-tags" type="text" v-model="form.tags" class="input" placeholder="如：查询,用户" />
              </div>
            </div>
            <label class="flex items-center gap-2">
              <input type="checkbox" v-model="form.require_confirm" />
              <span class="text-sm text-ink">执行前需人工确认</span>
            </label>
          </div>
          <template #footer>
            <button class="btn btn-secondary" @click="showForm = false">取消</button>
            <button class="btn btn-primary" @click="submitForm">{{ editingId ? '保存' : '创建' }}</button>
          </template>
        </base-modal>

        <!-- 测试 -->
        <base-modal v-model="showTest" :title="'测试 Skill：' + (testingSkill?.name || '')" width="max-w-2xl">
          <div class="space-y-3">
            <div>
              <label for="test-input" class="block text-sm font-medium text-ink mb-1">输入参数 (JSON)</label>
              <textarea id="test-input" v-model="testInput" class="textarea font-mono text-xs" rows="8"></textarea>
            </div>
            <div v-if="testResult">
              <p class="text-sm font-medium text-ink mb-1">执行结果：</p>
              <pre class="bg-surface-muted p-3 rounded-md text-xs font-mono overflow-x-auto">{{ JSON.stringify(testResult, null, 2) }}</pre>
            </div>
          </div>
          <template #footer>
            <button class="btn btn-secondary" @click="showTest = false">关闭</button>
            <button class="btn btn-primary" @click="runTest">▶ 执行测试</button>
          </template>
        </base-modal>
      </div>
    `
  }
})()
