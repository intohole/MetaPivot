/* Webhook 管理 — 列表 + 创建 + 删除 + 复制 URL + 测试触发
 * 外部系统通过 POST /api/v1/webhooks/{token} 触发 workflow/agent
 */
(function () {
  const { ref, reactive, onMounted, computed, nextTick } = Vue
  window.Pages = window.Pages || {}

  window.Pages.Webhooks = {
    name: 'WebhooksPage',
    setup() {
      const state = window.AppState
      const list = ref([])
      const loading = ref(false)

      const showForm = ref(false)
      const form = reactive({ name: '', target_type: 'workflow', target_id: '', secret: '' })
      const createdHook = ref(null)  // 创建成功后回填（含完整 token + URL）

      const isAdmin = computed(() => state.hasRole('admin'))

      const columns = computed(() => {
        const cols = [
          { key: 'name', label: '名称' },
          { key: 'target', label: '目标', width: '200px' },
          { key: 'token', label: 'Token', width: '180px' },
          { key: 'enabled', label: '状态', width: '80px' },
          { key: 'last_triggered_at', label: '最近触发', width: '160px' }
        ]
        if (isAdmin.value) cols.push({ key: 'actions', label: '操作', width: '180px', align: 'center' })
        return cols
      })

      const loadList = async () => {
        loading.value = true
        try {
          const res = await window.API.get('/webhooks')
          list.value = res || []
        } catch (e) { /* API 已弹 toast */ }
        finally { loading.value = false }
      }

      const openCreate = () => {
        Object.assign(form, { name: '', target_type: 'workflow', target_id: '', secret: '' })
        createdHook.value = null
        showForm.value = true
      }

      const submitForm = async () => {
        if (!form.name || !form.target_id) {
          state.notify('名称和目标 ID 必填', 'warning'); return
        }
        try {
          const res = await window.API.post('/webhooks', {
            name: form.name, target_type: form.target_type,
            target_id: form.target_id, secret: form.secret || null
          })
          // 创建成功后显示完整 URL（列表中 token 脱敏，这里展示完整）
          const origin = window.location.origin
          createdHook.value = {
            id: res.id, token: res.token,
            url: `${origin}/api/v1/webhooks/${res.token}`,
            target_type: res.target_type, target_id: res.target_id
          }
          state.notify('Webhook 创建成功', 'success')
          loadList()
        } catch (e) {
          state.notify('操作失败：' + (e.message || '未知错误'), 'error')
        }
      }

      const copyUrl = async (url) => {
        try {
          await navigator.clipboard.writeText(url)
          state.notify('已复制到剪贴板', 'success')
        } catch (e) { state.notify('复制失败，请手动复制', 'warning') }
      }

      const buildUrl = (token) => `${window.location.origin}/api/v1/webhooks/${token}`

      const testTrigger = async (hook) => {
        try {
          await window.API.post('/webhooks/' + hook.token, { message: 'test trigger' })
          state.notify('测试触发已发送', 'success')
          loadList()
        } catch (e) {
          state.notify('操作失败：' + (e.message || '未知错误'), 'error')
        }
      }

      const removeRow = async (row) => {
        const action = await state.confirmAction({
          title: '确认删除', message: `确认删除 Webhook "${row.name}"？关联的外部系统将无法再触发。`,
          confirmText: '删除', danger: true
        })
        if (action !== 'confirm') return
        try { await window.API.del('/webhooks/' + row.id); state.notify('已删除', 'success'); loadList() } catch (e) { state.notify('删除失败：' + (e.message || '未知错误'), 'error') }
      }

      const rotateToken = async (row) => {
        const action = await state.confirmAction({
          title: '轮换 Token', message: `确认轮换 "${row.name}" 的 token？旧 token 立即失效，需更新外部系统配置。`,
          confirmText: '轮换', danger: true
        })
        if (action !== 'confirm') return
        try {
          const res = await window.API.post(`/webhooks/${row.id}/rotate`)
          const origin = window.location.origin
          await copyUrl(`${origin}/api/v1/webhooks/${res.token}`)
          state.notify('Token 已轮换，新 URL 已复制', 'success')
          loadList()
        } catch (e) {
          state.notify('操作失败：' + (e.message || '未知错误'), 'error')
        }
      }

      onMounted(() => {
        loadList()
        if (state.pendingAction.value === 'create-webhook') {
          state.pendingAction.value = ''
          nextTick(() => openCreate())
        }
      })

      return {
        list, loading, columns, showForm, form, createdHook, isAdmin,
        loadList, openCreate, submitForm, copyUrl, buildUrl, testTrigger, removeRow, rotateToken, state
      }
    },
    template: `
      <div class="space-y-4">
        <base-card>
          <div class="flex flex-wrap gap-3 items-center">
            <div class="flex-1 min-w-[200px]">
              <p class="text-sm text-ink-muted">外部系统通过 HTTP POST 触发 workflow 或 agent。路径：<code class="text-xs bg-surface-muted px-1.5 py-0.5 rounded">/api/v1/webhooks/{token}</code></p>
            </div>
            <button v-if="isAdmin" class="btn btn-primary ml-auto" @click="openCreate">+ 新建 Webhook</button>
          </div>
        </base-card>

        <base-card>
          <base-table :columns="columns" :rows="list" :loading="loading">
            <template #name="{ row }">
              <p class="font-medium text-ink">{{ row.name }}</p>
            </template>
            <template #target="{ row }">
              <span class="badge badge-muted">{{ row.target_type }}</span>
              <span class="text-xs text-ink-muted ml-1">{{ row.target_id?.slice(0,8) }}...</span>
            </template>
            <template #token="{ row }">
              <code class="text-xs text-ink-muted">{{ row.token }}</code>
            </template>
            <template #enabled="{ row }">
              <span :class="['badge', row.enabled ? 'badge-success' : 'badge-muted']">{{ row.enabled ? '启用' : '禁用' }}</span>
            </template>
            <template #last_triggered_at="{ value }">
              <span class="text-xs text-ink-muted">{{ value?.slice(0,16) || '从未' }}</span>
            </template>
            <template #actions="{ row }">
              <div class="flex gap-1 justify-center">
                <button class="btn btn-ghost text-xs" @click="copyUrl(buildUrl(row.token))" title="复制 URL">📋</button>
                <button class="btn btn-ghost text-xs" @click="rotateToken(row)" title="轮换 Token">🔄</button>
                <button class="btn btn-ghost text-xs text-danger" @click="removeRow(row)" title="删除">🗑️</button>
              </div>
            </template>
          </base-table>
        </base-card>

        <!-- 创建 Webhook -->
        <base-modal v-model="showForm" :title="createdHook ? 'Webhook 创建成功' : '新建 Webhook'" width="max-w-2xl">
          <div v-if="!createdHook" class="space-y-4">
            <div>
              <label class="block text-sm font-medium text-ink mb-1">名称 *</label>
              <input type="text" v-model="form.name" class="input" placeholder="如：GitHub PR 触发器" />
            </div>
            <div class="grid grid-cols-2 gap-4">
              <div>
                <label class="block text-sm font-medium text-ink mb-1">目标类型 *</label>
                <select v-model="form.target_type" class="input">
                  <option value="workflow">workflow（触发工作流）</option>
                  <option value="agent">agent（触发 Agent 任务）</option>
                </select>
              </div>
              <div>
                <label class="block text-sm font-medium text-ink mb-1">目标 ID *</label>
                <input type="text" v-model="form.target_id" class="input" placeholder="workflow_id 或 agent 标识" />
              </div>
            </div>
            <div>
              <label class="block text-sm font-medium text-ink mb-1">HMAC 密钥（可选）</label>
              <input type="text" v-model="form.secret" class="input" placeholder="留空则不校验签名" />
              <p class="mt-1 text-xs text-ink-subtle">配置后外部请求需带 X-Webhook-Signature 头（HMAC-SHA256）</p>
            </div>
          </div>
          <div v-else class="space-y-3">
            <div class="card p-3 bg-green-50 border-green-200">
              <p class="text-sm text-green-900 font-medium">✅ Webhook 已创建，请复制以下 URL 配置到外部系统：</p>
            </div>
            <div>
              <label class="block text-sm font-medium text-ink mb-1">触发 URL</label>
              <div class="flex gap-2">
                <input type="text" readonly :value="createdHook.url" class="input flex-1 font-mono text-xs" />
                <button class="btn btn-primary" @click="copyUrl(createdHook.url)">📋 复制</button>
              </div>
            </div>
            <div class="flex gap-2">
              <button class="btn btn-secondary" @click="testTrigger(createdHook)">🧪 测试触发</button>
            </div>
          </div>
          <template #footer>
            <button v-if="!createdHook" class="btn btn-secondary" @click="showForm = false">取消</button>
            <button v-if="!createdHook" class="btn btn-primary" @click="submitForm">创建</button>
            <button v-else class="btn btn-primary" @click="showForm = false">完成</button>
          </template>
        </base-modal>
      </div>
    `
  }
})()
