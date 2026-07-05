/* 知识库管理 — 文档上传/列表/检索 */
(function () {
  const { ref, reactive, onMounted, computed } = Vue
  window.Pages = window.Pages || {}

  window.Pages.Knowledge = {
    name: 'KnowledgePage',
    setup() {
      const state = window.AppState
      const list = ref([])
      const total = ref(0)
      const page = ref(1)
      const pageSize = ref(20)
      const statusFilter = ref('')
      const loading = ref(false)

      const showUpload = ref(false)
      const uploadFile = ref(null)
      const uploadMeta = reactive({ description: '', tags: '' })
      const uploading = ref(false)

      const showSearch = ref(false)
      const searchQuery = ref('')
      const searchTopK = ref(5)
      const searchResults = ref([])

      const columns = [
        { key: 'filename', label: '文件名' },
        { key: 'status', label: '状态', width: '100px' },
        { key: 'chunk_count', label: '分块数', width: '100px' },
        { key: 'created_at', label: '上传时间', width: '160px' },
        { key: 'actions', label: '操作', width: '100px', align: 'center' }
      ]

      const loadList = async () => {
        loading.value = true
        try {
          const res = await window.API.get('/knowledge/documents', {
            page: page.value, page_size: pageSize.value, status: statusFilter.value
          })
          list.value = res.items || []
          total.value = res.total || 0
        } finally { loading.value = false }
      }

      const onFileChange = (e) => {
        const f = e.target.files[0]
        if (f) uploadFile.value = f
      }

      const submitUpload = async () => {
        if (!uploadFile.value) { state.notify('请选择文件', 'warning'); return }
        uploading.value = true
        try {
          const fd = new FormData()
          fd.append('file', uploadFile.value)
          fd.append('metadata', JSON.stringify({
            description: uploadMeta.description, tags: uploadMeta.tags
          }))
          const res = await window.API.upload('/knowledge/documents', fd)
          state.notify('文件已上传，正在处理：' + res.filename, 'success')
          showUpload.value = false
          uploadFile.value = null
          uploadMeta.description = ''
          uploadMeta.tags = ''
          loadList()
        } catch (e) {} finally { uploading.value = false }
      }

      const removeRow = async (row) => {
        const action = await state.confirmAction({
          title: '确认删除', message: '确认删除文档 "' + row.filename + '"？此操作不可撤销。',
          confirmText: '删除', danger: true
        })
        if (action !== 'confirm') return
        try {
          await window.API.del('/knowledge/documents/' + row.id)
          state.notify('已删除', 'success')
          loadList()
        } catch (e) {}
      }

      const runSearch = async () => {
        if (!searchQuery.value.trim()) { state.notify('请输入检索内容', 'warning'); return }
        try {
          const res = await window.API.post('/knowledge/search', {
            query: searchQuery.value, top_k: searchTopK.value
          })
          searchResults.value = res.results || []
          if (searchResults.value.length === 0) state.notify('未检索到相关内容', 'info')
        } catch (e) {}
      }

      const onPageChange = (p) => { page.value = p; loadList() }
      const onSearch = () => { page.value = 1; loadList() }

      onMounted(loadList)

      return {
        list, total, page, pageSize, statusFilter, loading, columns,
        showUpload, uploadFile, uploadMeta, uploading,
        showSearch, searchQuery, searchTopK, searchResults,
        loadList, onFileChange, submitUpload, removeRow, runSearch,
        onPageChange, onSearch, state
      }
    },
    template: `
      <div class="space-y-4">
        <base-card>
          <div class="flex flex-wrap gap-3 items-center">
            <select v-model="statusFilter" class="select w-40" @change="onSearch" aria-label="状态筛选">
              <option value="">全部状态</option>
              <option value="processing">处理中</option>
              <option value="ready">就绪</option>
              <option value="failed">失败</option>
            </select>
            <button class="btn btn-secondary" @click="onSearch">搜索</button>
            <button class="btn btn-secondary ml-4" @click="showSearch = true">🔍 知识检索</button>
            <button class="btn btn-primary ml-auto" @click="showUpload = true">+ 上传文档</button>
          </div>
        </base-card>

        <base-card title="文档列表">
          <base-table :columns="columns" :rows="list" :loading="loading">
            <template #filename="{ row }">
              <p class="font-medium text-ink">📄 {{ row.filename }}</p>
              <p v-if="row.metadata?.description" class="text-xs text-ink-muted">{{ row.metadata.description }}</p>
            </template>
            <template #status="{ value }">
              <span :class="['badge', value === 'ready' ? 'badge-success' : value === 'processing' ? 'badge-info' : 'badge-danger']">{{ value }}</span>
            </template>
            <template #chunk_count="{ value }"><span class="text-sm">{{ value || 0 }}</span></template>
            <template #created_at="{ value }"><span class="text-xs text-ink-muted">{{ value?.slice(0, 16) }}</span></template>
            <template #actions="{ row }">
              <button class="btn btn-ghost text-xs text-danger" @click="removeRow(row)" title="删除">🗑️</button>
            </template>
          </base-table>
          <pagination :page="page" :page-size="pageSize" :total="total" @change="onPageChange" />
        </base-card>

        <!-- 上传 -->
        <base-modal v-model="showUpload" title="上传知识文档" width="max-w-lg">
          <div class="space-y-3">
            <div>
              <label for="doc-file" class="block text-sm font-medium text-ink mb-1">选择文件 *</label>
              <input id="doc-file" type="file" class="input" @change="onFileChange"
                     accept=".txt,.md,.pdf,.docx,.doc,.html" />
              <p class="mt-1 text-xs text-ink-subtle">支持 txt / md / pdf / docx / html，单个文件 ≤ 20MB</p>
            </div>
            <div>
              <label for="doc-desc" class="block text-sm font-medium text-ink mb-1">描述</label>
              <textarea id="doc-desc" v-model="uploadMeta.description" class="textarea" rows="2" placeholder="文档简要说明"></textarea>
            </div>
            <div>
              <label for="doc-tags" class="block text-sm font-medium text-ink mb-1">标签（逗号分隔）</label>
              <input id="doc-tags" type="text" v-model="uploadMeta.tags" class="input" placeholder="如：HR,制度" />
            </div>
          </div>
          <template #footer>
            <button class="btn btn-secondary" @click="showUpload = false">取消</button>
            <button class="btn btn-primary" @click="submitUpload" :disabled="uploading">
              {{ uploading ? '上传中...' : '上传' }}
            </button>
          </template>
        </base-modal>

        <!-- 检索 -->
        <base-modal v-model="showSearch" title="知识库检索" width="max-w-2xl">
          <div class="space-y-3">
            <div class="flex gap-2">
              <input v-model="searchQuery" type="text" class="input flex-1" placeholder="输入检索内容" @keydown.enter="runSearch" aria-label="检索内容" />
              <input v-model.number="searchTopK" type="number" min="1" max="20" class="input w-20" aria-label="返回数量" />
              <button class="btn btn-primary" @click="runSearch">检索</button>
            </div>
            <div v-if="searchResults.length > 0" class="space-y-2">
              <div v-for="(r, i) in searchResults" :key="i" class="card p-3">
                <div class="flex items-center justify-between mb-1">
                  <span class="text-xs text-ink-subtle">文档: {{ r.document_id?.slice(0, 8) }}...</span>
                  <span class="badge badge-info">相似度: {{ (r.score * 100).toFixed(1) }}%</span>
                </div>
                <p class="text-sm text-ink whitespace-pre-wrap">{{ r.content }}</p>
              </div>
            </div>
            <empty-state v-else-if="searchQuery" icon="🔍" title="未检索到内容" description="请尝试其他关键词" />
            <empty-state v-else icon="📚" title="知识检索" description="输入问题，从知识库中检索相关内容" />
          </div>
        </base-modal>
      </div>
    `
  }
})()
