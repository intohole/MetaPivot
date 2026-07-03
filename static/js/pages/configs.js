/* 系统配置 — 配置项列表 + 编辑 */
(function () {
  const { ref, reactive, onMounted, computed } = Vue
  window.Pages = window.Pages || {}

  window.Pages.Configs = {
    name: 'ConfigsPage',
    setup() {
      const state = window.AppState
      const list = ref([])
      const categories = ref([])
      const activeCategory = ref('')
      const loading = ref(false)
      const keyword = ref('')

      const showEdit = ref(false)
      const editingKey = ref('')
      const editValue = ref('')
      const editingItem = ref(null)

      const filteredList = computed(() => {
        if (!keyword.value) return list.value
        const kw = keyword.value.toLowerCase()
        return list.value.filter(item =>
          item.key?.toLowerCase().includes(kw) ||
          item.description?.toLowerCase().includes(kw)
        )
      })

      const columns = [
        { key: 'key', label: '配置键' },
        { key: 'value', label: '配置值' },
        { key: 'category', label: '分类', width: '120px' },
        { key: 'description', label: '说明' },
        { key: 'actions', label: '操作', width: '100px', align: 'center' }
      ]

      const loadList = async () => {
        loading.value = true
        try {
          const res = await window.API.get('/configs', { category: activeCategory.value })
          list.value = res.items || []
          // 提取分类
          const cats = new Set()
          list.value.forEach(item => { if (item.category) cats.add(item.category) })
          categories.value = Array.from(cats)
        } finally { loading.value = false }
      }

      const openEdit = (row) => {
        editingItem.value = row
        editingKey.value = row.key
        editValue.value = row.value || ''
        showEdit.value = true
      }

      const submitEdit = async () => {
        try {
          await window.API.put('/configs/' + editingKey.value, { value: editValue.value })
          state.notify('配置已更新：' + editingKey.value, 'success')
          showEdit.value = false
          loadList()
        } catch (e) {}
      }

      const onCategoryChange = () => loadList()
      const onSearch = () => { /* computed 自动响应 */ }

      onMounted(loadList)

      return {
        list, filteredList, categories, activeCategory, loading, columns, keyword,
        showEdit, editingKey, editValue, editingItem,
        loadList, openEdit, submitEdit, onCategoryChange, onSearch, state
      }
    },
    template: `
      <div class="space-y-4">
        <base-card>
          <div class="flex flex-wrap gap-3 items-center">
            <input v-model="keyword" type="text" class="input flex-1 min-w-[200px]" placeholder="搜索配置项" aria-label="搜索" />
            <select v-model="activeCategory" class="select w-40" @change="onCategoryChange" aria-label="分类筛选">
              <option value="">全部分类</option>
              <option v-for="c in categories" :key="c" :value="c">{{ c }}</option>
            </select>
            <button class="btn btn-secondary" @click="loadList">⟳ 刷新</button>
          </div>
        </base-card>

        <base-card title="配置项列表" :subtitle="'共 ' + filteredList.length + ' 项'">
          <base-table :columns="columns" :rows="filteredList" :loading="loading">
            <template #key="{ value }">
              <span class="font-mono text-xs text-ink">{{ value }}</span>
            </template>
            <template #value="{ value }">
              <span class="font-mono text-xs text-ink-muted break-all">{{ value?.length > 60 ? value.slice(0, 60) + '...' : value }}</span>
            </template>
            <template #category="{ value }">
              <span class="badge badge-muted">{{ value || '通用' }}</span>
            </template>
            <template #description="{ value }">
              <span class="text-xs text-ink-muted">{{ value || '-' }}</span>
            </template>
            <template #actions="{ row }">
              <button v-if="row.updatable" class="btn btn-ghost text-xs" @click="openEdit(row)" title="编辑" :disabled="!row.updatable">✏️</button>
              <span v-else class="text-xs text-ink-subtle" title="不可修改">🔒</span>
            </template>
          </base-table>
        </base-card>

        <!-- 编辑配置 -->
        <base-modal v-model="showEdit" :title="'编辑配置：' + editingKey" width="max-w-lg">
          <div class="space-y-3">
            <div v-if="editingItem">
              <p class="text-xs text-ink-subtle mb-1">{{ editingItem.description || '无说明' }}</p>
              <p class="text-xs text-ink-subtle">分类：{{ editingItem.category || '通用' }}</p>
            </div>
            <div>
              <label for="cfg-value" class="block text-sm font-medium text-ink mb-1">配置值</label>
              <textarea id="cfg-value" v-model="editValue" class="textarea font-mono text-xs" rows="6"></textarea>
            </div>
            <div v-if="editingItem" class="text-xs text-amber-700 bg-amber-50 p-2 rounded">
              ⚠️ 修改敏感配置可能影响服务运行，部分配置需重启服务后生效
            </div>
          </div>
          <template #footer>
            <button class="btn btn-secondary" @click="showEdit = false">取消</button>
            <button class="btn btn-primary" @click="submitEdit">保存</button>
          </template>
        </base-modal>
      </div>
    `
  }
})()
