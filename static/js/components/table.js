/* ============================================================
   table.js — BaseTable 干净重写 + Pagination 增强
   挂载：window.Components.BaseTable / window.Components.Pagination
   特性：selectable/sortable/stickyHeader/row-click + 页码按钮+跳页+pageSize
   ============================================================ */
(function () {
  const { computed, ref } = Vue
  const Components = window.Components || (window.Components = {})

  /* --- 通用表格（Phase 3 干净重写） ---
   * 列定义：{ key, label, width?, align?, sortable? }
   * 插槽：#<key>="{ row, value, index }" 自定义单元格
   * selectable：显示复选列，通过 v-model:selected 双向绑定已选 key 数组
   * sortable：列头点击触发 @sort({ key, order })，order 为 '' | 'asc' | 'desc'
   */
  Components.BaseTable = {
    name: 'BaseTable',
    props: {
      columns: { type: Array, required: true },
      rows: { type: Array, default: () => [] },
      loading: { type: Boolean, default: false },
      empty: { type: String, default: '暂无数据' },
      selectable: { type: Boolean, default: false },
      rowKey: { type: String, default: 'id' },
      selectedKeys: { type: Array, default: () => [] },
      stickyHeader: { type: Boolean, default: true },
      maxHeight: { type: String, default: '' }
    },
    emits: ['update:selected', 'row-click', 'sort'],
    setup(props, { emit }) {
      const sortKey = ref('')
      const sortOrder = ref('')

      const allSelected = computed(() =>
        props.rows.length > 0 && props.rows.every(r => props.selectedKeys.includes(r[props.rowKey]))
      )
      const indeterminate = computed(() =>
        !allSelected.value && props.rows.some(r => props.selectedKeys.includes(r[props.rowKey]))
      )

      const toggleAll = (e) => {
        const keys = e.target.checked ? props.rows.map(r => r[props.rowKey]) : []
        emit('update:selected', keys)
      }
      const toggleRow = (key) => {
        const idx = props.selectedKeys.indexOf(key)
        const next = [...props.selectedKeys]
        if (idx >= 0) next.splice(idx, 1); else next.push(key)
        emit('update:selected', next)
      }
      const isSelected = (key) => props.selectedKeys.includes(key)

      const onSort = (col) => {
        if (!col.sortable) return
        if (sortKey.value !== col.key) { sortKey.value = col.key; sortOrder.value = 'asc' }
        else if (sortOrder.value === 'asc') sortOrder.value = 'desc'
        else { sortKey.value = ''; sortOrder.value = '' }
        emit('sort', { key: sortKey.value, order: sortOrder.value })
      }
      const sortIcon = (col) => {
        if (!col.sortable) return ''
        if (sortKey.value !== col.key) return '↕'
        return sortOrder.value === 'asc' ? '↑' : '↓'
      }
      const ariaSort = (col) => {
        if (!col.sortable) return undefined
        if (sortKey.value !== col.key) return 'none'
        return sortOrder.value === 'asc' ? 'ascending' : 'descending'
      }

      return { allSelected, indeterminate, toggleAll, toggleRow, isSelected, onSort, sortIcon, ariaSort }
    },
    template: `
      <div class="relative" role="region" aria-label="数据表格">
        <div :class="['overflow-x-auto', stickyHeader ? 'overflow-y-auto' : '']" :style="maxHeight ? 'max-height:' + maxHeight : ''">
          <table class="min-w-full divide-y divide-border" role="table">
            <thead :class="['bg-surface-muted', stickyHeader ? 'sticky top-0 z-10' : '']">
              <tr>
                <th v-if="selectable" scope="col" class="px-4 py-3 w-10">
                  <input type="checkbox" :checked="allSelected" :indeterminate.prop="indeterminate"
                         @change="toggleAll" aria-label="全选当前页" class="rounded border-border" />
                </th>
                <th v-for="c in columns" :key="c.key" scope="col"
                    :style="c.width ? 'width:' + c.width : ''"
                    :aria-sort="ariaSort(c)"
                    :class="['px-4 py-3 text-xs font-medium text-ink-muted uppercase tracking-wider select-none',
                             c.align === 'center' ? 'text-center' : 'text-left',
                             c.sortable ? 'cursor-pointer hover:text-ink' : '']"
                    @click="onSort(c)">
                  <span class="inline-flex items-center gap-1">
                    {{ c.label }}
                    <span v-if="c.sortable" aria-hidden="true" class="text-ink-subtle">{{ sortIcon(c) }}</span>
                  </span>
                </th>
              </tr>
            </thead>
            <tbody class="bg-surface divide-y divide-border">
              <tr v-if="loading">
                <td :colspan="columns.length + (selectable ? 1 : 0)" class="px-4 py-8">
                  <div class="animate-pulse space-y-2" role="status" aria-label="加载中">
                    <div v-for="r in 4" :key="r" class="flex gap-4">
                      <div v-for="c in columns" :key="c.key" class="flex-1 h-4 bg-surface-muted rounded"></div>
                    </div>
                    <span class="sr-only">加载中...</span>
                  </div>
                </td>
              </tr>
              <tr v-else-if="rows.length === 0">
                <td :colspan="columns.length + (selectable ? 1 : 0)" class="px-4 py-12 text-center">
                  <div class="flex flex-col items-center gap-2 text-ink-subtle" role="status">
                    <span class="text-4xl" aria-hidden="true">📭</span>
                    <span class="text-sm">{{ empty }}</span>
                  </div>
                </td>
              </tr>
              <tr v-for="(row, idx) in rows" :key="row[rowKey] || idx"
                  :class="['transition-colors', isSelected(row[rowKey]) ? 'bg-brand-light/30' : 'hover:bg-surface-muted']"
                  @click="emit('row-click', row)">
                <td v-if="selectable" class="px-4 py-3" @click.stop>
                  <input type="checkbox" :checked="isSelected(row[rowKey])"
                         @change="toggleRow(row[rowKey])" :aria-label="'选择行 ' + idx" class="rounded border-border" />
                </td>
                <td v-for="c in columns" :key="c.key"
                    :class="['px-4 py-3 text-sm text-ink', c.align === 'center' ? 'text-center' : '']">
                  <slot :name="c.key" :row="row" :value="row[c.key]" :index="idx">{{ row[c.key] }}</slot>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    `
  }

  /* --- 增强分页（页码按钮+省略号+pageSize+跳页） ---
   * @change emit { page, pageSize } — pageSize 变更时自动回到第 1 页
   */
  Components.Pagination = {
    name: 'Pagination',
    props: {
      page: { type: Number, required: true },
      pageSize: { type: Number, required: true },
      total: { type: Number, required: true },
      pageSizes: { type: Array, default: () => [10, 20, 50, 100] },
      showJumper: { type: Boolean, default: true },
      showTotal: { type: Boolean, default: true }
    },
    emits: ['change'],
    setup(props, { emit }) {
      const totalPages = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)))
      // 智能页码：≤7 全显示；否则首尾固定 + 当前页±1 + 省略号
      const pageButtons = computed(() => {
        const tp = totalPages.value, cur = props.page
        if (tp <= 7) return Array.from({ length: tp }, (_, i) => i + 1)
        const pages = [1]
        if (cur > 4) pages.push('...')
        const start = Math.max(2, cur - 1), end = Math.min(tp - 1, cur + 1)
        for (let i = start; i <= end; i++) pages.push(i)
        if (cur < tp - 3) pages.push('...')
        pages.push(tp)
        return pages
      })
      const jumpInput = ref('')
      const go = (p) => {
        if (p >= 1 && p <= totalPages.value && p !== props.page) {
          emit('change', { page: p, pageSize: props.pageSize })
        }
      }
      const changePageSize = (e) => emit('change', { page: 1, pageSize: Number(e.target.value) })
      const doJump = () => {
        const p = parseInt(jumpInput.value, 10)
        if (!isNaN(p)) go(p)
        jumpInput.value = ''
      }
      return { totalPages, pageButtons, jumpInput, go, changePageSize, doJump }
    },
    template: `
      <nav v-if="total > 0" class="flex flex-wrap items-center justify-between gap-3 py-3" aria-label="分页">
        <div class="flex items-center gap-3 text-sm text-ink-muted">
          <span v-if="showTotal">共 {{ total }} 条 / 第 {{ page }}/{{ totalPages }} 页</span>
          <label class="flex items-center gap-1">
            <span class="sr-only">每页条数</span>
            <select :value="pageSize" @change="changePageSize"
                    class="text-sm border border-border rounded px-2 py-1 bg-surface text-ink focus:ring-2 focus:ring-brand">
              <option v-for="ps in pageSizes" :key="ps" :value="ps">{{ ps }} / 页</option>
            </select>
          </label>
        </div>
        <div class="flex items-center gap-1">
          <button class="btn btn-ghost px-2 py-1 text-sm" :disabled="page <= 1" @click="go(page - 1)" aria-label="上一页">‹</button>
          <template v-for="(p, idx) in pageButtons" :key="idx">
            <span v-if="p === '...'" class="px-2 text-ink-subtle" aria-hidden="true">…</span>
            <button v-else @click="go(p)" :aria-current="p === page ? 'page' : undefined"
                    :aria-label="'第 ' + p + ' 页'"
                    :class="['min-w-[32px] px-2 py-1 text-sm rounded transition-colors',
                             p === page ? 'bg-brand text-white' : 'text-ink hover:bg-surface-muted']">{{ p }}</button>
          </template>
          <button class="btn btn-ghost px-2 py-1 text-sm" :disabled="page >= totalPages" @click="go(page + 1)" aria-label="下一页">›</button>
          <label v-if="showJumper && totalPages > 5" class="flex items-center gap-1 ml-2 text-sm text-ink-muted">
            <span>跳至</span>
            <input v-model="jumpInput" type="number" min="1" :max="totalPages" @keyup.enter="doJump"
                   class="w-14 text-sm border border-border rounded px-2 py-1 bg-surface text-ink focus:ring-2 focus:ring-brand" />
            <span>页</span>
          </label>
        </div>
      </nav>
    `
  }
})()
