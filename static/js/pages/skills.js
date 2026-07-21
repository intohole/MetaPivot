/* Skill 管理页 — 列表 + 创建/编辑/启用禁用/测试 */
(function () {
  const { ref, reactive, onMounted, computed, nextTick } = Vue
  window.Pages = window.Pages || {}

  window.Pages.Skills = {
    name: 'SkillsPage',
    setup() {
      const state = window.AppState
      const sourceType = ref('')
      const scope = ref('all')

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

      // Skill 健康度 + 手动优化（覆盖 GET /skills/{id}/health + POST /skills/{id}/optimize）
      const showHealth = ref(false)
      const healthSkill = ref(null)
      const healthResult = ref(null)
      const healthLoading = ref(false)
      const optimizing = ref(false)

      // Sprint 8.3: 批量操作
      const selectedKeys = ref([])
      const bulkLoading = ref(false)

      // useListPage 统一分页加载/搜索/翻页（消除重复样板）；翻页时清空批量选择
      const lp = window.useListPage('/skills', {
        failMsg: '加载 Skill 列表失败',
        extraParams: () => ({ source_type: sourceType.value, scope: scope.value }),
        onPageChange: () => { selectedKeys.value = [] }
      })
      const { list, total, page, pageSize, keyword, loading, loadList, onPageChange, onSearch } = lp

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
        if (isAdmin.value) cols.push({ key: 'actions', label: '操作', width: '260px', align: 'center' })
        return cols
      })

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
        } catch (e) {
          state.notify('保存 Skill 失败：' + (e.message || '未知错误'), 'error')
        }
      }

      const toggleEnable = async (row) => {
        // Sprint 8.4: 乐观更新 — 立即切换 UI，失败回滚
        const oldEnabled = row.enabled
        row.enabled = !row.enabled
        try {
          await window.API.post('/skills/' + row.id + '/' + (oldEnabled ? 'disable' : 'enable'))
          state.notify(oldEnabled ? '已禁用' : '已启用', 'success')
        } catch (e) {
          row.enabled = oldEnabled  // 回滚
          state.notify('操作失败：' + (e.message || '未知错误'), 'error')
        }
      }

      // Sprint 6.1: 检测 skill 是否被熔断（changelog 末尾 circuit_breaker 标记 + enabled=false）
      const isCircuitBroken = (row) => {
        if (!row.changelog || !Array.isArray(row.changelog) || row.changelog.length === 0) return false
        const last = row.changelog[row.changelog.length - 1]
        return last && last.source === 'circuit_breaker'
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
        } catch (e) {
          state.notify('操作失败：' + (e.message || '未知错误'), 'error')
        }
      }

      // Phase 3: 发布到团队 + scope 切换
      const publishToTeam = async (row) => {
        try {
          await window.API.post('/skills/' + row.id + '/publish')
          state.notify('已发布到团队', 'success')
          loadList()
        } catch (e) {
          state.notify('操作失败：' + (e.message || '未知错误'), 'error')
        }
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

      // 健康度详情：GET /skills/{id}/health → {total_executions, failed_executions, failure_rate, health, circuit_broken}
      const openHealth = async (row) => {
        healthSkill.value = row
        healthResult.value = null
        showHealth.value = true
        healthLoading.value = true
        try {
          healthResult.value = await window.API.get('/skills/' + row.id + '/health')
        } catch (e) {
          state.notify('加载健康度失败：' + (e.message || '未知错误'), 'error')
        } finally { healthLoading.value = false }
      }

      const healthBadgeClass = (h) => ({
        healthy: 'badge-success',
        degraded: 'badge-warning',
        critical: 'badge-danger'
      }[h] || 'badge-muted')

      const healthLabel = (h) => ({ healthy: '健康', degraded: '降级', critical: '严重' }[h] || '未知')

      // 手动触发自进化优化：POST /skills/{id}/optimize（异步分析 + 生成优化建议）
      const optimizeSkill = async (row) => {
        const act = await state.confirmAction({
          title: '触发 Skill 优化',
          message: '确认对 "' + row.name + '" 触发自进化优化？系统将分析近期执行记录并生成优化建议（草稿需在 Review 页审批）。',
          confirmText: '触发优化'
        })
        if (act !== 'confirm') return
        optimizing.value = true
        try {
          await window.API.post('/skills/' + row.id + '/optimize')
          state.notify('优化已触发，请稍后到自进化 Review 查看建议', 'success')
        } catch (e) {
          state.notify('优化失败：' + (e.message || '未知错误'), 'error')
        } finally { optimizing.value = false }
      }

      // Sprint 8.3: 批量操作（启用/禁用/删除）— 并行调用单条 API，汇总结果
      const bulkAction = async (action, label) => {
        if (selectedKeys.value.length === 0) return
        const action_ = await state.confirmAction({
          title: '批量' + label,
          message: `确认对 ${selectedKeys.value.length} 个 Skill 执行"${label}"操作？`,
          confirmText: label, danger: action === 'delete',
        })
        if (action_ !== 'confirm') return
        bulkLoading.value = true
        try {
          const results = await Promise.allSettled(
            selectedKeys.value.map(id => {
              if (action === 'enable' || action === 'disable') {
                return window.API.post(`/skills/${id}/${action}`)
              } else if (action === 'delete') {
                return window.API.del(`/skills/${id}`)
              }
            })
          )
          const ok = results.filter(r => r.status === 'fulfilled').length
          const fail = results.length - ok
          state.notify(`批量${label}完成：成功 ${ok} 个${fail > 0 ? '，失败 ' + fail + ' 个' : ''}`, fail > 0 ? 'warning' : 'success')
          selectedKeys.value = []
          loadList()
        } catch (e) {
          state.notify(`批量${label}失败：` + (e.message || '未知错误'), 'error')
        } finally {
          bulkLoading.value = false
        }
      }
      const bulkEnable = () => bulkAction('enable', '启用')
      const bulkDisable = () => bulkAction('disable', '禁用')
      const bulkDelete = () => bulkAction('delete', '删除')

      onMounted(() => {
        loadList()
        if (state.pendingAction.value === 'create-skill') {
          state.pendingAction.value = ''
          nextTick(() => openCreate())
        }
        // NL 中枢：search-skill: 前缀触发搜索
        if (state.pendingAction.value.startsWith('search-skill:')) {
          keyword.value = state.pendingAction.value.slice('search-skill:'.length)
          state.pendingAction.value = ''
          onSearch()
        }
      })

      return {
        list, total, page, pageSize, keyword, sourceType, scope, loading, columns,
        showForm, editingId, form, isAdmin, showTest, testingSkill, testInput, testResult,
        showHealth, healthSkill, healthResult, healthLoading, optimizing,
        loadList, openCreate, openEdit, submitForm, toggleEnable, removeRow, isCircuitBroken,
        openTest, runTest, openHealth, optimizeSkill, healthBadgeClass, healthLabel,
        onPageChange, onSearch, onScopeChange, publishToTeam, state,
        selectedKeys, bulkLoading, bulkEnable, bulkDisable, bulkDelete
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
            <button class="btn btn-ghost ml-auto" @click="state.navigate('/skill-review')" title="草稿与修订审批">📝 自进化 Review</button>
            <button v-if="isAdmin" class="btn btn-primary" @click="openCreate">+ 新建 Skill</button>
          </div>
        </base-card>

        <!-- 列表 -->
        <base-card>
          <!-- Sprint 8.3: 批量操作栏 -->
          <div v-if="selectedKeys.length > 0" class="flex items-center gap-3 px-4 py-2 bg-brand-light/50 border-b border-brand/20 rounded-t-lg">
            <span class="text-sm text-ink">已选 {{ selectedKeys.length }} 项</span>
            <button class="btn btn-ghost text-xs" @click="selectedKeys = []">取消选择</button>
            <div class="flex-1"></div>
            <button class="btn btn-secondary text-xs" @click="bulkEnable" :disabled="bulkLoading">✓ 批量启用</button>
            <button class="btn btn-secondary text-xs" @click="bulkDisable" :disabled="bulkLoading">✕ 批量禁用</button>
            <button v-if="isAdmin" class="btn btn-ghost text-xs text-danger" @click="bulkDelete" :disabled="bulkLoading">🗑️ 批量删除</button>
          </div>
          <base-table :columns="columns" :rows="list" :loading="loading"
                      :empty="keyword ? '未找到匹配的 Skill' : '暂无 Skill，点击右上角「+ 新建 Skill」创建'"
                      selectable row-key="id" v-model:selected="selectedKeys">
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
              <div class="flex items-center gap-1">
                <switch v-if="isAdmin" :model-value="row.enabled" @update:model-value="() => toggleEnable(row)" :aria-label="(row.enabled ? '禁用' : '启用') + ' ' + row.name" size="sm" />
                <span v-else :class="['badge', row.enabled ? 'badge-success' : 'badge-muted']">{{ row.enabled ? '启用' : '禁用' }}</span>
                <span v-if="!row.enabled && isCircuitBroken(row)" class="badge badge-danger" title="失败率过高已自动禁用，修复后可手动启用">⛔ 熔断</span>
              </div>
            </template>
            <template #actions="{ row }">
              <div class="flex gap-1 justify-center">
                <button class="btn btn-ghost text-xs" @click="openTest(row)" title="测试">🧪</button>
                <button class="btn btn-ghost text-xs" @click="openHealth(row)" title="健康度">🩺</button>
                <button v-if="isAdmin" class="btn btn-ghost text-xs" @click="optimizeSkill(row)" :disabled="optimizing" title="触发自进化优化">🚀</button>
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
                  <option value="tenant_manager">管理者</option>
                  <option value="tenant_admin">仅管理员</option>
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

        <!-- 健康度详情：GET /skills/{id}/health -->
        <base-modal v-model="showHealth" :title="'Skill 健康度：' + (healthSkill?.name || '')" width="max-w-lg">
          <div v-if="healthLoading" class="text-center py-6 text-sm text-ink-muted">加载中...</div>
          <div v-else-if="healthResult" class="space-y-3">
            <div class="flex items-center gap-3">
              <span :class="['badge', healthBadgeClass(healthResult.health)]">{{ healthLabel(healthResult.health) }}</span>
              <span v-if="healthResult.circuit_broken" class="badge badge-danger">⛔ 已熔断</span>
              <span v-else class="text-xs text-ink-subtle">未熔断</span>
            </div>
            <div class="grid grid-cols-3 gap-3 text-center">
              <div class="card p-3">
                <p class="text-xs text-ink-subtle">总执行次数</p>
                <p class="text-lg font-semibold text-ink mt-1">{{ healthResult.total_executions || 0 }}</p>
              </div>
              <div class="card p-3">
                <p class="text-xs text-ink-subtle">失败次数</p>
                <p class="text-lg font-semibold text-danger mt-1">{{ healthResult.failed_executions || 0 }}</p>
              </div>
              <div class="card p-3">
                <p class="text-xs text-ink-subtle">失败率</p>
                <p class="text-lg font-semibold mt-1" :class="(healthResult.failure_rate || 0) >= 0.6 ? 'text-danger' : (healthResult.failure_rate || 0) >= 0.3 ? 'text-warning' : 'text-success'">{{ ((healthResult.failure_rate || 0) * 100).toFixed(1) }}%</p>
              </div>
            </div>
            <p class="text-xs text-ink-subtle">健康等级说明：健康（失败率 &lt; 30%）| 降级（30%-60%）| 严重（&ge; 60%，触发自动熔断）</p>
          </div>
          <empty-state v-else icon="🩺" title="暂无健康度数据" description="该 Skill 尚未产生执行记录" />
          <template #footer>
            <button class="btn btn-secondary" @click="showHealth = false">关闭</button>
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
