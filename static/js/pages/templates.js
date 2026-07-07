/* 工作流模板库 — SOP Gallery：分类筛选 + 一键实例化 */
(function () {
  const { ref, reactive, computed, onMounted } = Vue
  window.Pages = window.Pages || {}

  const CATEGORY_META = {
    daily: { label: '日常', icon: '☀️', color: 'badge-warning' },
    weekly: { label: '周期', icon: '📆', color: 'badge-info' },
    report: { label: '报告', icon: '📄', color: 'badge-success' },
    notification: { label: '通知', icon: '🔔', color: 'badge-muted' },
    communication: { label: '沟通', icon: '💬', color: 'badge-info' },
    general: { label: '通用', icon: '⚙️', color: 'badge-muted' },
  }

  window.Pages.Templates = {
    name: 'TemplatesPage',
    setup() {
      const state = window.AppState
      const list = ref([])
      const total = ref(0)
      const loading = ref(false)
      const keyword = ref('')
      const activeCategory = ref('')

      const showInstantiate = ref(false)
      const currentTpl = ref(null)
      const instForm = reactive({ name: '', trigger_overrides: {} })

      const categories = computed(() => {
        const map = {}
        list.value.forEach(t => { map[t.category] = (map[t.category] || 0) + 1 })
        return Object.keys(map).map(k => ({ key: k, count: map[k], ...CATEGORY_META[k] || CATEGORY_META.general }))
      })

      const isAdmin = computed(() => state.hasRole('admin'))

      const loadList = async () => {
        loading.value = true
        try {
          const params = { page: 1, page_size: 100, keyword: keyword.value }
          if (activeCategory.value) params.category = activeCategory.value
          const res = await window.API.get('/workflows/templates', params)
          list.value = res.items || []
          total.value = res.total || 0
        } catch (e) {
          state.notify('加载模板列表失败：' + (e.message || '未知错误'), 'error')
        } finally { loading.value = false }
      }

      const onSearch = () => { loadList() }
      const selectCategory = (cat) => { activeCategory.value = activeCategory.value === cat ? '' : cat; loadList() }

      const openInstantiate = (tpl) => {
        currentTpl.value = tpl
        instForm.name = ''
        // 预填触发器覆盖：保留模板默认配置，用户可改
        const tt = tpl.trigger_template || {}
        instForm.trigger_overrides = { type: tt.type || 'manual', cron_expr: tt.cron_expr || '' }
        showInstantiate.value = true
      }

      const submitInstantiate = async () => {
        if (!currentTpl.value) return
        try {
          const payload = {
            name: instForm.name || '',
            trigger_overrides: instForm.trigger_overrides,
          }
          const res = await window.API.post('/workflows/templates/' + currentTpl.value.id + '/instantiate', payload)
          state.notify('已从模板创建工作流：' + (res.name || ''), 'success')
          showInstantiate.value = false
          // 跳转到工作流页查看
          state.navigate('/workflows')
        } catch (e) {
          state.notify('实例化失败：' + (e.message || '未知错误'), 'error')
        }
      }

      const removeTemplate = async (tpl) => {
        const action = await state.confirmAction({
          title: '确认删除', message: '确认删除模板 "' + tpl.name + '"？此操作不可撤销。',
          confirmText: '删除', danger: true,
        })
        if (action !== 'confirm') return
        try {
          await window.API.del('/workflows/templates/' + tpl.id)
          state.notify('模板已删除', 'success')
          loadList()
        } catch (e) {
          state.notify('删除失败：' + (e.message || '未知错误'), 'error')
        }
      }

      const catLabel = (c) => (CATEGORY_META[c] || CATEGORY_META.general).label
      const catIcon = (c) => (CATEGORY_META[c] || CATEGORY_META.general).icon
      const catColor = (c) => (CATEGORY_META[c] || CATEGORY_META.general).color
      const triggerLabel = (tt) => {
        if (!tt || !tt.type || tt.type === 'manual') return '手动'
        if (tt.type === 'schedule') return '定时 ' + (tt.cron_expr || '')
        if (tt.type === 'webhook') return 'Webhook'
        return tt.type
      }
      const goWorkflows = () => state.navigate('/workflows')

      onMounted(() => { loadList() })

      return {
        state, list, total, loading, keyword, activeCategory, categories, isAdmin,
        showInstantiate, currentTpl, instForm,
        loadList, onSearch, selectCategory, openInstantiate, submitInstantiate, removeTemplate,
        catLabel, catIcon, catColor, triggerLabel, goWorkflows,
      }
    },
    template: `
      <div class="space-y-4">
        <base-card>
          <div class="flex flex-wrap gap-3 items-center">
            <input v-model="keyword" type="text" class="input flex-1 min-w-[200px]" placeholder="搜索模板名称或描述" @keydown.enter="onSearch" aria-label="搜索模板" />
            <button class="btn btn-secondary" @click="onSearch">搜索</button>
            <button class="btn btn-ghost ml-auto" @click="goWorkflows" title="返回工作流列表">← 返回工作流</button>
          </div>
          <!-- 分类筛选 -->
          <div class="flex flex-wrap gap-2 mt-3">
            <button :class="['badge cursor-pointer transition', activeCategory==='' ? 'badge-success' : 'badge-muted hover:badge-info']" @click="selectCategory('')">全部 ({{ total }})</button>
            <button v-for="c in categories" :key="c.key"
                    :class="['badge cursor-pointer transition flex items-center gap-1', activeCategory===c.key ? 'badge-success' : c.color + ' hover:opacity-80']"
                    @click="selectCategory(c.key)">
              <span>{{ c.icon }}</span> {{ c.label }} ({{ c.count }})
            </button>
          </div>
        </base-card>

        <base-card>
          <div v-if="loading" class="py-12 text-center text-ink-muted">加载中…</div>
          <div v-else-if="list.length === 0" class="py-12 text-center">
            <empty-state icon="📭" title="暂无模板" description="团队 SOP 模板将在此展示，可一键实例化为工作流" />
          </div>
          <div v-else class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <div v-for="tpl in list" :key="tpl.id" class="border border-border rounded-lg p-4 hover:border-brand hover:shadow-sm transition group flex flex-col">
              <div class="flex items-start justify-between mb-2">
                <div class="flex items-center gap-2">
                  <span class="text-2xl" aria-hidden="true">{{ catIcon(tpl.category) }}</span>
                  <div>
                    <p class="font-semibold text-ink group-hover:text-brand">{{ tpl.name }}</p>
                    <span :class="['badge text-xs mt-0.5', catColor(tpl.category)]">{{ catLabel(tpl.category) }}</span>
                  </div>
                </div>
                <span v-if="tpl.usage_count > 0" class="text-xs text-ink-subtle">用过 {{ tpl.usage_count }} 次</span>
              </div>
              <p class="text-sm text-ink-muted flex-1 mb-3 line-clamp-3">{{ tpl.description || '无描述' }}</p>
              <div class="flex flex-wrap gap-1 mb-3">
                <span v-for="tag in (tpl.tags || []).slice(0,4)" :key="tag" class="badge badge-muted text-xs">#{{ tag }}</span>
              </div>
              <div class="flex items-center justify-between pt-2 border-t border-border">
                <span class="text-xs text-ink-subtle">⚡ {{ triggerLabel(tpl.trigger_template) }}</span>
                <div class="flex gap-1">
                  <button class="btn btn-primary text-xs" @click="openInstantiate(tpl)" title="实例化为工作流">⚡ 使用</button>
                  <button v-if="isAdmin" class="btn btn-ghost text-xs text-danger" @click="removeTemplate(tpl)" title="删除">🗑️</button>
                </div>
              </div>
            </div>
          </div>
        </base-card>

        <!-- 实例化弹窗 -->
        <base-modal v-model="showInstantiate" :title="'从模板创建工作流：' + (currentTpl?.name || '')" width="max-w-2xl">
          <div v-if="currentTpl" class="space-y-4">
            <div class="card p-3 bg-blue-50 border-blue-200">
              <p class="text-sm text-blue-900"><strong>模板说明：</strong>{{ currentTpl.description }}</p>
              <p class="text-xs text-blue-700 mt-1">触发方式：{{ triggerLabel(currentTpl.trigger_template) }} | 分类：{{ catLabel(currentTpl.category) }}</p>
            </div>
            <div>
              <label for="inst-name" class="block text-sm font-medium text-ink mb-1">工作流名称</label>
              <input id="inst-name" type="text" v-model="instForm.name" class="input" :placeholder="currentTpl.name + ' (实例)'" />
              <p class="text-xs text-ink-subtle mt-1">留空则自动命名为「{{ currentTpl.name }} (实例)」</p>
            </div>
            <div>
              <label class="block text-sm font-medium text-ink mb-1">触发器配置</label>
              <div class="flex flex-wrap gap-3 items-center">
                <label class="flex items-center gap-1">
                  <input type="radio" v-model="instForm.trigger_overrides.type" value="manual" />
                  <span class="text-sm">手动</span>
                </label>
                <label class="flex items-center gap-1">
                  <input type="radio" v-model="instForm.trigger_overrides.type" value="schedule" />
                  <span class="text-sm">定时</span>
                </label>
                <label class="flex items-center gap-1">
                  <input type="radio" v-model="instForm.trigger_overrides.type" value="webhook" />
                  <span class="text-sm">Webhook</span>
                </label>
                <input v-if="instForm.trigger_overrides.type === 'schedule'" type="text" v-model="instForm.trigger_overrides.cron_expr" class="input flex-1 min-w-[180px] font-mono text-xs" placeholder="*/5 * * * *（分 时 日 月 周）" />
                <p v-if="instForm.trigger_overrides.type === 'webhook'" class="text-xs text-ink-subtle w-full">保存后自动生成 Webhook URL（在 Webhook 管理页查看）</p>
              </div>
            </div>
            <div v-if="currentTpl.input_schema && currentTpl.input_schema.properties" class="card p-3 bg-surface-muted">
              <p class="text-sm font-medium text-ink mb-1">📋 运行时输入参数（执行工作流时填写）</p>
              <div class="space-y-1">
                <div v-for="(schema, key) in currentTpl.input_schema.properties" :key="key" class="flex items-center gap-2 text-xs">
                  <code class="px-1.5 py-0.5 bg-surface rounded text-brand">{{ key }}</code>
                  <span class="text-ink-muted">{{ schema.description || '' }}</span>
                  <span v-if="(currentTpl.input_schema.required || []).includes(key)" class="badge badge-warning text-xs">必填</span>
                </div>
              </div>
            </div>
          </div>
          <template #footer>
            <button class="btn btn-secondary" @click="showInstantiate = false">取消</button>
            <button class="btn btn-primary" @click="submitInstantiate">⚡ 创建工作流</button>
          </template>
        </base-modal>
      </div>
    `
  }
})()
