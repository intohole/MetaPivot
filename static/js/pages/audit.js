/* 审计日志 — 列表 + 统计图表 + 筛选 */
(function () {
  const { ref, reactive, onMounted, computed } = Vue
  window.Pages = window.Pages || {}

  window.Pages.Audit = {
    name: 'AuditPage',
    setup() {
      const state = window.AppState
      const list = ref([])
      const total = ref(0)
      const page = ref(1)
      const pageSize = ref(20)
      const filters = reactive({ user_id: '', action: '', skill_id: '', start_time: '', end_time: '' })
      const loading = ref(false)

      const stats = ref([])
      const statsGroupBy = ref('day')
      const totalCalls = ref(0)

      const columns = [
        { key: 'created_at', label: '时间', width: '160px' },
        { key: 'user_id', label: '用户', width: '120px' },
        { key: 'action', label: '操作', width: '140px' },
        { key: 'skill_id', label: 'Skill', width: '140px' },
        { key: 'duration_ms', label: '耗时(ms)', width: '100px' },
        { key: 'status', label: '状态', width: '100px' },
        { key: 'output_summary', label: '摘要' }
      ]

      const loadList = async () => {
        loading.value = true
        try {
          const res = await window.API.get('/audit/logs', {
            page: page.value, page_size: pageSize.value,
            ...filters
          })
          list.value = res.items || []
          total.value = res.total || 0
        } finally { loading.value = false }
      }

      const loadStats = async () => {
        try {
          const res = await window.API.get('/audit/stats', { group_by: statsGroupBy.value })
          stats.value = res.stats || []
          totalCalls.value = res.total || 0
        } catch (e) {}
      }

      const maxCount = computed(() => Math.max(...stats.value.map(s => s.count), 1))

      const onPageChange = ({ page: p, pageSize: ps }) => { page.value = p; if (ps) pageSize.value = ps; loadList() }
      const onSearch = () => { page.value = 1; loadList() }
      const onGroupChange = () => loadStats()

      const resetFilters = () => {
        Object.assign(filters, { user_id: '', action: '', skill_id: '', start_time: '', end_time: '' })
        loadList()
      }

      onMounted(() => { loadList(); loadStats() })

      return {
        list, total, page, pageSize, filters, loading, columns,
        stats, statsGroupBy, totalCalls, maxCount,
        loadList, loadStats, onPageChange, onSearch, onGroupChange, resetFilters, state
      }
    },
    template: `
      <div class="space-y-4">
        <!-- 统计 -->
        <base-card title="调用统计" subtitle="按时间维度统计">
          <template #action>
            <select v-model="statsGroupBy" class="select w-32" @change="onGroupChange" aria-label="分组">
              <option value="day">按日</option>
              <option value="user">按用户</option>
              <option value="skill">按 Skill</option>
            </select>
          </template>
          <div v-if="stats.length > 0" class="space-y-2">
            <div v-for="s in stats.slice(0, 10)" :key="s.key" class="flex items-center gap-3">
              <span class="text-xs text-ink-muted w-32 truncate">{{ s.key }}</span>
              <div class="flex-1 bg-surface-muted rounded h-6 overflow-hidden" role="img" :aria-label="s.key + ': ' + s.count + ' 次'">
                <div class="bg-brand h-full flex items-center px-2 text-xs text-white" :style="{ width: (s.count / maxCount * 100) + '%' }">
                  <span v-if="s.count / maxCount > 0.3">{{ s.count }}</span>
                </div>
              </div>
              <span class="text-xs text-ink-muted w-20">{{ s.success_rate ? (s.success_rate * 100).toFixed(0) + '%' : '-' }}</span>
              <span class="text-xs text-ink-subtle w-20">{{ s.avg_duration_ms ? s.avg_duration_ms + 'ms' : '' }}</span>
            </div>
            <p class="text-xs text-ink-subtle pt-2 border-t border-border">总调用次数：{{ totalCalls }}（仅显示前 10 条）</p>
          </div>
          <empty-state v-else icon="📊" title="暂无统计数据" />
        </base-card>

        <!-- 筛选 -->
        <base-card title="日志查询">
          <template #action>
            <button class="btn btn-ghost text-sm" @click="resetFilters">重置</button>
          </template>
          <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 mb-4">
            <input v-model="filters.user_id" type="text" class="input" placeholder="用户 ID" aria-label="用户 ID" />
            <input v-model="filters.action" type="text" class="input" placeholder="操作类型" aria-label="操作类型" />
            <input v-model="filters.skill_id" type="text" class="input" placeholder="Skill ID" aria-label="Skill ID" />
            <input v-model="filters.start_time" type="datetime-local" class="input" aria-label="开始时间" />
            <input v-model="filters.end_time" type="datetime-local" class="input" aria-label="结束时间" />
            <button class="btn btn-primary" @click="onSearch">查询</button>
          </div>

          <base-table :columns="columns" :rows="list" :loading="loading">
            <template #created_at="{ value }"><span class="text-xs text-ink-muted">{{ value?.slice(0, 19).replace('T', ' ') }}</span></template>
            <template #user_id="{ value }"><span class="font-mono text-xs">{{ value?.slice(0, 8) }}</span></template>
            <template #action="{ value }"><span class="badge badge-info">{{ value }}</span></template>
            <template #skill_id="{ value }"><span class="font-mono text-xs text-ink-muted">{{ value?.slice(0, 8) || '-' }}</span></template>
            <template #duration_ms="{ value }"><span class="text-sm">{{ value || 0 }}</span></template>
            <template #status="{ value }">
              <span :class="['badge', value === 'success' ? 'badge-success' : value === 'failed' ? 'badge-danger' : 'badge-muted']">{{ value }}</span>
            </template>
            <template #output_summary="{ value }"><span class="text-xs text-ink-muted truncate">{{ value || '-' }}</span></template>
          </base-table>
          <pagination :page="page" :page-size="pageSize" :total="total" @change="onPageChange" />
        </base-card>
      </div>
    `
  }
})()
