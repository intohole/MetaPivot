/* 用户管理 — 列表 + 创建/编辑 + 启用禁用 */
(function () {
  const { ref, reactive, onMounted, computed } = Vue
  window.Pages = window.Pages || {}

  window.Pages.Users = {
    name: 'UsersPage',
    setup() {
      const state = window.AppState
      const list = ref([])
      const total = ref(0)
      const page = ref(1)
      const pageSize = ref(20)
      const keyword = ref('')
      const roleFilter = ref('')
      const loading = ref(false)

      const showForm = ref(false)
      const editingId = ref('')
      const form = reactive({
        username: '', password: '', role: 'user',
        im_accounts: '{}', status: 'active'
      })

      const roles = ref([])

      const columns = computed(() => [
        { key: 'username', label: '用户名' },
        { key: 'role', label: '角色', width: '100px' },
        { key: 'status', label: '状态', width: '80px' },
        { key: 'im_accounts', label: 'IM 账号', width: '200px' },
        { key: 'created_at', label: '创建时间', width: '160px' },
        { key: 'actions', label: '操作', width: '140px', align: 'center' }
      ])

      const loadList = async () => {
        loading.value = true
        try {
          const res = await window.API.get('/users', {
            page: page.value, page_size: pageSize.value,
            keyword: keyword.value, role: roleFilter.value
          })
          list.value = res.items || []
          total.value = res.total || 0
        } finally { loading.value = false }
      }

      const loadRoles = async () => {
        try {
          const res = await window.API.get('/roles')
          roles.value = res.items || []
        } catch (e) {
          state.notify('操作失败：' + (e.message || '未知错误'), 'error')
        }
      }

      const openCreate = () => {
        Object.assign(form, { username: '', password: '', role: 'user', im_accounts: '{}', status: 'active' })
        editingId.value = ''
        showForm.value = true
      }

      const openEdit = (row) => {
        Object.assign(form, {
          username: row.username, password: '',
          role: row.role,
          im_accounts: JSON.stringify(row.im_accounts || {}, null, 2),
          status: row.status || 'active'
        })
        editingId.value = row.id
        showForm.value = true
      }

      const submitForm = async () => {
        try {
          let imAccounts = {}
          try { imAccounts = JSON.parse(form.im_accounts) } catch (e) {
            state.notify('IM 账号不是合法 JSON', 'error'); return
          }
          const payload = {
            role: form.role, im_accounts: imAccounts, status: form.status
          }
          if (form.password) payload.password = form.password
          if (editingId.value) {
            await window.API.put('/users/' + editingId.value, payload)
            state.notify('用户更新成功', 'success')
          } else {
            if (!form.username || !form.password) {
              state.notify('用户名和密码必填', 'warning'); return
            }
            payload.username = form.username
            payload.password = form.password
            await window.API.post('/users', payload)
            state.notify('用户创建成功', 'success')
          }
          showForm.value = false
          loadList()
        } catch (e) {
          state.notify('操作失败：' + (e.message || '未知错误'), 'error')
        }
      }

      const toggleStatus = async (row) => {
        try {
          await window.API.put('/users/' + row.id, { status: row.status === 'active' ? 'disabled' : 'active' })
          state.notify(row.status === 'active' ? '已禁用' : '已启用', 'success')
          loadList()
        } catch (e) {
          state.notify('操作失败：' + (e.message || '未知错误'), 'error')
        }
      }

      const onPageChange = ({ page: p, pageSize: ps }) => { page.value = p; if (ps) pageSize.value = ps; loadList() }
      const onSearch = () => { page.value = 1; loadList() }

      onMounted(() => { loadList(); loadRoles() })

      return {
        list, total, page, pageSize, keyword, roleFilter, loading, columns, roles,
        showForm, editingId, form,
        loadList, openCreate, openEdit, submitForm, toggleStatus,
        onPageChange, onSearch, state
      }
    },
    template: `
      <div class="space-y-4">
        <base-card>
          <div class="flex flex-wrap gap-3 items-center">
            <input v-model="keyword" type="text" class="input flex-1 min-w-[200px]" placeholder="搜索用户名" @keydown.enter="onSearch" aria-label="搜索" />
            <select v-model="roleFilter" class="select w-40" @change="onSearch" aria-label="角色筛选">
              <option value="">全部角色</option>
              <option value="admin">管理员</option>
              <option value="manager">管理者</option>
              <option value="user">普通用户</option>
            </select>
            <button class="btn btn-secondary" @click="onSearch">搜索</button>
            <button class="btn btn-primary ml-auto" @click="openCreate">+ 新建用户</button>
          </div>
        </base-card>

        <base-card>
          <base-table :columns="columns" :rows="list" :loading="loading">
            <template #username="{ row }">
              <p class="font-medium text-ink">{{ row.username }}</p>
              <p class="text-xs text-ink-subtle font-mono">{{ row.id?.slice(0, 8) }}</p>
            </template>
            <template #role="{ value }">
              <span :class="['badge', value === 'admin' ? 'badge-danger' : value === 'manager' ? 'badge-warning' : 'badge-info']">{{ value }}</span>
            </template>
            <template #status="{ value }">
              <span :class="['badge', value === 'active' ? 'badge-success' : 'badge-muted']">{{ value === 'active' ? '启用' : '禁用' }}</span>
            </template>
            <template #im_accounts="{ value }">
              <span class="text-xs text-ink-muted font-mono">{{ JSON.stringify(value || {}).slice(0, 40) }}</span>
            </template>
            <template #created_at="{ value }"><span class="text-xs text-ink-muted">{{ value?.slice(0, 16) }}</span></template>
            <template #actions="{ row }">
              <div class="flex gap-1 justify-center">
                <button class="btn btn-ghost text-xs" @click="openEdit(row)" title="编辑">✏️</button>
                <button class="btn btn-ghost text-xs" @click="toggleStatus(row)" :title="row.status === 'active' ? '禁用' : '启用'">{{ row.status === 'active' ? '⏸️' : '▶️' }}</button>
              </div>
            </template>
          </base-table>
          <pagination :page="page" :page-size="pageSize" :total="total" @change="onPageChange" />
        </base-card>

        <!-- 创建/编辑 -->
        <base-modal v-model="showForm" :title="editingId ? '编辑用户' : '新建用户'" width="max-w-lg">
          <div class="space-y-4">
            <div>
              <label for="u-name" class="block text-sm font-medium text-ink mb-1">用户名 *</label>
              <input id="u-name" type="text" v-model="form.username" class="input" :disabled="!!editingId" placeholder="登录用户名" />
            </div>
            <div>
              <label for="u-pwd" class="block text-sm font-medium text-ink mb-1">密码 {{ editingId ? '（留空不修改）' : '*' }}</label>
              <input id="u-pwd" type="password" v-model="form.password" class="input" placeholder="登录密码" autocomplete="new-password" />
            </div>
            <div class="grid grid-cols-2 gap-4">
              <div>
                <label for="u-role" class="block text-sm font-medium text-ink mb-1">角色</label>
                <select id="u-role" v-model="form.role" class="select">
                  <option value="user">普通用户</option>
                  <option value="manager">管理者</option>
                  <option value="admin">管理员</option>
                </select>
              </div>
              <div>
                <label for="u-status" class="block text-sm font-medium text-ink mb-1">状态</label>
                <select id="u-status" v-model="form.status" class="select">
                  <option value="active">启用</option>
                  <option value="disabled">禁用</option>
                </select>
              </div>
            </div>
            <div>
              <label for="u-im" class="block text-sm font-medium text-ink mb-1">IM 账号绑定 (JSON)</label>
              <textarea id="u-im" v-model="form.im_accounts" class="textarea font-mono text-xs" rows="4" placeholder='{"dingtalk": "user_id", "feishu": "open_id"}'></textarea>
              <p class="mt-1 text-xs text-ink-subtle">格式：{ "dingtalk": "用户ID", "feishu": "open_id", "wecom": "userid" }</p>
            </div>
          </div>
          <template #footer>
            <button class="btn btn-secondary" @click="showForm = false">取消</button>
            <button class="btn btn-primary" @click="submitForm">{{ editingId ? '保存' : '创建' }}</button>
          </template>
        </base-modal>
      </div>
    `
  }
})()
