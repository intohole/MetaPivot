/* ============================================================
   通用组件库 — 挂载到 window.Components
   所有组件均符合 AI 可访问性清单（aria/label/focus）
   ============================================================ */
(function () {
  const { computed, onMounted, onUnmounted, ref, watch, nextTick } = Vue
  const Components = {}

  /* --- 顶部加载进度条 --- */
  Components.LoadingBar = {
    template: `
      <div v-if="loading" role="status" aria-live="polite"
           class="fixed top-0 left-0 right-0 h-1 z-50 bg-brand">
        <div class="h-full w-1/3 bg-brand-dark animate-pulse"></div>
        <span class="sr-only">加载中</span>
      </div>
    `,
    setup() {
      return { loading: window.AppState.loading }
    }
  }

  /* --- Toast 通知容器 --- */
  Components.ToastContainer = {
    template: `
      <div class="fixed top-4 right-4 z-50 space-y-2" role="region" aria-label="通知">
        <transition-group name="fade">
          <div v-for="t in toasts" :key="t.id"
               :class="['card px-4 py-3 min-w-[280px] max-w-md flex items-start gap-3 shadow-md', typeClass(t.type)]"
               role="alert">
            <span aria-hidden="true" class="text-lg">{{ icon(t.type) }}</span>
            <p class="flex-1 text-sm text-ink">{{ t.message }}</p>
            <button @click="dismiss(t.id)" class="text-ink-subtle hover:text-ink" aria-label="关闭通知">×</button>
          </div>
        </transition-group>
      </div>
    `,
    setup() {
      const { toasts, dismissToast } = window.AppState
      const typeClass = (t) => ({
        info: 'border-l-4 border-l-blue-500',
        success: 'border-l-4 border-l-green-500',
        warning: 'border-l-4 border-l-amber-500',
        error: 'border-l-4 border-l-red-500'
      }[t] || '')
      const icon = (t) => ({ info: 'ℹ️', success: '✅', warning: '⚠️', error: '❌' }[t] || 'ℹ️')
      return { toasts, dismiss: dismissToast, typeClass, icon }
    }
  }

  /* --- 卡片容器 --- */
  Components.BaseCard = {
    props: { title: String, subtitle: String, action: { type: Boolean, default: false } },
    template: `
      <section class="card p-6">
        <header v-if="title || action" class="flex items-center justify-between mb-4">
          <div>
            <h3 class="text-lg font-semibold text-ink">{{ title }}</h3>
            <p v-if="subtitle" class="mt-1 text-sm text-ink-muted">{{ subtitle }}</p>
          </div>
          <div v-if="action"><slot name="action" /></div>
        </header>
        <slot />
      </section>
    `
  }

  /* --- 空状态 --- */
  Components.EmptyState = {
    props: { icon: { type: String, default: '📭' }, title: String, description: String },
    template: `
      <div class="flex flex-col items-center justify-center py-12 text-center" role="status">
        <div class="text-5xl mb-4" aria-hidden="true">{{ icon }}</div>
        <h3 class="text-base font-medium text-ink">{{ title || '暂无数据' }}</h3>
        <p v-if="description" class="mt-1 text-sm text-ink-muted">{{ description }}</p>
        <div v-if="$slots.action" class="mt-4"><slot name="action" /></div>
      </div>
    `
  }

  /* --- 状态徽章（agent/workflow 状态映射） --- */
  Components.StatusBadge = {
    props: { status: String },
    setup(props) {
      const map = {
        pending: ['badge-muted', '待处理'], planning: ['badge-info', '规划中'],
        executing: ['badge-info', '执行中'], running: ['badge-info', '运行中'],
        waiting_confirm: ['badge-warning', '待确认'], paused: ['badge-warning', '已暂停'],
        completed: ['badge-success', '已完成'], failed: ['badge-danger', '失败'],
        cancelled: ['badge-muted', '已取消'], processing: ['badge-info', '处理中']
      }
      const cfg = computed(() => map[props.status] || ['badge-muted', props.status || '未知'])
      return { cfg }
    },
    template: `<span :class="['badge', cfg[0]]"><span class="w-1.5 h-1.5 rounded-full bg-current" aria-hidden="true"></span>{{ cfg[1] }}</span>`
  }

  /* --- 分页 --- */
  Components.Pagination = {
    props: { page: Number, pageSize: Number, total: Number },
    emits: ['change'],
    setup(props, { emit }) {
      const totalPages = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)))
      const go = (p) => { if (p >= 1 && p <= totalPages.value) emit('change', p) }
      return { totalPages, go }
    },
    template: `
      <nav v-if="total > 0" class="flex items-center justify-between py-3" aria-label="分页">
        <p class="text-sm text-ink-muted">共 {{ total }} 条 / 第 {{ page }}/{{ totalPages }} 页</p>
        <div class="flex gap-1">
          <button class="btn btn-ghost" :disabled="page <= 1" @click="go(page - 1)" aria-label="上一页">上一页</button>
          <button class="btn btn-ghost" :disabled="page >= totalPages" @click="go(page + 1)" aria-label="下一页">下一页</button>
        </div>
      </nav>
    `
  }

  /* --- 通用表格 --- */
  Components.BaseTable = {
    props: {
      columns: { type: Array, required: true },  // [{key, label, width, align}]
      rows: { type: Array, default: () => [] },
      empty: { type: String, default: '暂无数据' },
      loading: { type: Boolean, default: false }
    },
    template: `
      <div class="overflow-x-auto" role="region" aria-label="数据表格">
        <table class="min-w-full divide-y divide-border">
          <thead class="bg-surface-muted">
            <tr>
              <th v-for="c in columns" :key="c.key"
                  :style="c.width ? 'width:' + c.width : ''"
                  :class="['px-4 py-3 text-left text-xs font-medium text-ink-muted uppercase tracking-wider', c.align === 'center' ? 'text-center' : '']">
                {{ c.label }}
              </th>
            </tr>
          </thead>
          <tbody class="bg-surface divide-y divide-border">
            <tr v-if="loading">
              <td :colspan="columns.length" class="px-4 py-8 text-center text-ink-muted">
                <span role="status">加载中...</span>
              </td>
            </tr>
            <tr v-else-if="rows.length === 0">
              <td :colspan="columns.length" class="px-4 py-8 text-center text-ink-muted">{{ empty }}</td>
            </tr>
            <tr v-for="(row, idx) in rows" :key="row.id || idx" class="hover:bg-surface-muted transition-colors">
              <td v-for="c in columns" :key="c.key"
                  :class="['px-4 py-3 text-sm text-ink', c.align === 'center' ? 'text-center' : '']">
                <slot :name="c.key" :row="row" :value="row[c.key]">{{ row[c.key] }}</slot>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    `
  }

  /* --- 通用模态框（Round 5: focus trap + 背景 inert，a11y 闭环）--- */
  Components.BaseModal = {
    props: {
      modelValue: { type: Boolean, default: false },
      title: String, width: { type: String, default: 'max-w-lg' }
    },
    emits: ['update:modelValue', 'close'],
    setup(props, { emit }) {
      const close = () => { emit('update:modelValue', false); emit('close') }
      const rootRef = ref(null)
      let releaseTrap = null
      // focus trap：打开时锁定焦点 + 背景设 inert；关闭时释放 + 恢复焦点
      watch(() => props.modelValue, async (v) => {
        if (v) {
          await nextTick()
          if (rootRef.value && window.trapFocus) {
            releaseTrap = window.trapFocus(rootRef.value)
          }
        } else if (releaseTrap) {
          releaseTrap()
          releaseTrap = null
        }
      })
      const onKey = (e) => { if (e.key === 'Escape' && props.modelValue) close() }
      onMounted(() => document.addEventListener('keydown', onKey))
      onUnmounted(() => {
        document.removeEventListener('keydown', onKey)
        if (releaseTrap) releaseTrap()
      })
      return { close, rootRef }
    },
    template: `
      <transition name="fade">
        <div v-if="modelValue" ref="rootRef" class="fixed inset-0 z-40 flex items-center justify-center p-4" role="dialog" aria-modal="true" :aria-label="title">
          <div class="absolute inset-0 bg-black/50" @click="close" aria-hidden="true"></div>
          <div :class="['relative card w-full shadow-modal', width]">
            <header class="flex items-center justify-between px-6 py-4 border-b border-border">
              <h3 class="text-lg font-semibold text-ink">{{ title }}</h3>
              <button @click="close" class="text-ink-subtle hover:text-ink text-2xl leading-none" aria-label="关闭">×</button>
            </header>
            <div class="px-6 py-4 max-h-[70vh] overflow-y-auto"><slot /></div>
            <footer v-if="$slots.footer" class="px-6 py-4 border-t border-border flex justify-end gap-2"><slot name="footer" /></footer>
          </div>
        </div>
      </transition>
    `
  }

  /* --- 确认对话框 --- */
  Components.ConfirmDialog = {
    props: { modelValue: Boolean, title: { type: String, default: '确认操作' }, message: String, confirmText: { type: String, default: '确认' }, danger: Boolean },
    emits: ['update:modelValue', 'confirm', 'cancel'],
    setup(props, { emit }) {
      const handle = (action) => { emit(action); emit('update:modelValue', false) }
      return { handle }
    },
    template: `
      <base-modal :model-value="modelValue" @update:model-value="v => $emit('update:modelValue', v)" :title="title" width="max-w-md">
        <p class="text-sm text-ink">{{ message }}</p>
        <template #footer>
          <button class="btn btn-secondary" @click="handle('cancel')">取消</button>
          <button :class="['btn', danger ? 'btn-danger' : 'btn-primary']" @click="handle('confirm')">{{ confirmText }}</button>
        </template>
      </base-modal>
    `
  }

  /* --- 表单字段渲染器（schema 驱动） --- */
  Components.FormField = {
    props: { field: Object, model: Object },
    template: `
      <div class="space-y-1">
        <label v-if="field.label" :for="field.key" class="block text-sm font-medium text-ink">
          {{ field.label }}<span v-if="field.required" class="text-danger ml-0.5" aria-label="必填">*</span>
        </label>
        <input v-if="field.type === 'text' || field.type === 'password' || field.type === 'number'"
               :id="field.key" :type="field.type" v-model="model[field.key]"
               :placeholder="field.placeholder || ''" :aria-invalid="field.error ? 'true' : 'false'"
               :aria-describedby="field.error ? field.key + '-err' : undefined"
               class="input" />
        <textarea v-else-if="field.type === 'textarea'" :id="field.key" v-model="model[field.key]"
                  :rows="field.rows || 3" :placeholder="field.placeholder || ''" class="textarea"></textarea>
        <select v-else-if="field.type === 'select'" :id="field.key" v-model="model[field.key]" class="select">
          <option value="">请选择</option>
          <option v-for="o in field.options" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
        <p v-if="field.hint && !field.error" :id="field.key + '-hint'" class="text-xs text-ink-subtle">{{ field.hint }}</p>
        <p v-if="field.error" :id="field.key + '-err'" class="text-xs text-danger" role="alert">{{ field.error }}</p>
      </div>
    `
  }

  /* --- Round 4: Skeleton 骨架屏（加载态优化，替代"加载中..."文本） --- */
  Components.Skeleton = {
    props: {
      lines: { type: Number, default: 3 },
      height: { type: String, default: 'h-4' },
      avatar: { type: Boolean, default: false }
    },
    template: `
      <div class="space-y-3 animate-pulse" role="status" aria-label="加载中">
        <div v-if="avatar" class="flex items-center gap-3">
          <div class="w-10 h-10 rounded-full bg-surface-muted"></div>
          <div class="flex-1 space-y-2">
            <div class="h-3 bg-surface-muted rounded w-1/4"></div>
            <div class="h-3 bg-surface-muted rounded w-1/2"></div>
          </div>
        </div>
        <div v-for="i in lines" :key="i" :class="['bg-surface-muted rounded', height]"></div>
        <span class="sr-only">加载中...</span>
      </div>
    `
  }

  /* --- Round 4: 表格骨架屏（行/列骨架） --- */
  Components.TableSkeleton = {
    props: { rows: { type: Number, default: 5 }, cols: { type: Number, default: 4 } },
    template: `
      <div class="animate-pulse" role="status" aria-label="表格加载中">
        <div v-for="r in rows" :key="r" class="flex gap-4 py-3 border-b border-border">
          <div v-for="c in cols" :key="c" class="flex-1 h-4 bg-surface-muted rounded"></div>
        </div>
        <span class="sr-only">加载中...</span>
      </div>
    `
  }

  window.Components = Components
})()
