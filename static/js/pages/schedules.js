/* 定时任务管理 — 列表 + 取消
 * 覆盖后端 GET /schedules（列表）、DELETE /schedules/{task_id}（取消）
 * 字段：id, message, description, run_at, recurring, cron_expr, next_run_at, status, channel, chat_id, retry_count, max_retries, last_error, last_run_at, created_at
 */
(function () {
  const { ref, reactive, onMounted, computed } = Vue
  window.Pages = window.Pages || {}

  window.Pages.Schedules = {
    name: 'SchedulesPage',
    setup() {
      const state = window.AppState
      // useListPage 统一分页加载/翻页（消除重复样板）
      const lp = window.useListPage('/schedules', { failMsg: '加载定时任务失败', withKeyword: false })
      const { list, total, page, pageSize, loading, loadList, onPageChange } = lp

      const columns = computed(() => [
        { key: 'id', label: 'ID', width: '60px' },
        { key: 'message', label: '触发消息' },
        { key: 'trigger', label: '触发方式', width: '160px' },
        { key: 'next_run_at', label: '下次执行', width: '160px' },
        { key: 'status', label: '状态', width: '90px' },
        { key: 'actions', label: '操作', width: '100px', align: 'center' }
      ])

      const cancelTask = async (row) => {
        const act = await state.confirmAction({
          title: '取消定时任务', message: '确认取消任务 #' + row.id + '？此操作不可撤销。',
          confirmText: '取消任务', danger: true
        })
        if (act !== 'confirm') return
        try {
          await window.API.del('/schedules/' + row.id)
          state.notify('任务已取消', 'success')
          loadList()
        } catch (e) { state.notify('取消失败：' + (e.message || ''), 'error') }
      }

      // 触发方式渲染：cron_expr 优先 > run_at 一次性 > recurring 周期
      const triggerInfo = (row) => {
        if (row.cron_expr) return { label: 'Cron', detail: row.cron_expr, cls: 'badge-info' }
        if (row.run_at) return { label: '一次性', detail: row.run_at?.slice(0, 16), cls: 'badge-warning' }
        const map = { daily: '每日', weekly: '每周', monthly: '每月' }
        return { label: map[row.recurring] || row.recurring || '未知', detail: '', cls: 'badge-muted' }
      }

      const statusBadge = (s) => ({
        pending: 'badge-info', running: 'badge-warning',
        completed: 'badge-success', failed: 'badge-danger', cancelled: 'badge-muted'
      }[s] || 'badge-muted')

      const statusLabel = (s) => ({
        pending: '待执行', running: '执行中', completed: '已完成', failed: '失败', cancelled: '已取消'
      }[s] || s)

      const fmtTime = window.Format.time

      const goDlq = () => state.navigate('/dlq')

      onMounted(() => loadList())

      return {
        list, total, page, pageSize, loading, columns,
        loadList, cancelTask, onPageChange,
        triggerInfo, statusBadge, statusLabel, fmtTime, goDlq, state
      }
    },
    template: `
      <div class="space-y-4">
        <base-card>
          <div class="flex flex-wrap items-center gap-3">
            <div>
              <h2 class="text-lg font-semibold text-ink">定时任务</h2>
              <p class="text-xs text-ink-subtle mt-0.5">Agent 自动解析的定时任务 + 手动创建的周期任务</p>
            </div>
            <div class="ml-auto flex gap-2">
              <button class="btn btn-secondary text-sm" @click="loadList" title="刷新">🔄 刷新</button>
              <button class="btn btn-ghost text-sm" @click="goDlq" title="查看死信队列">⚠️ 死信队列</button>
            </div>
          </div>
        </base-card>

        <base-card>
          <base-table :columns="columns" :rows="list" :loading="loading"
                      :empty="'暂无定时任务。Agent 对话中说「每天 9 点提醒我」即可自动创建。'">
            <template #id="{ value }"><span class="text-xs text-ink-subtle">#{{ value }}</span></template>
            <template #message="{ row }">
              <p class="text-sm text-ink line-clamp-2" :title="row.message">{{ row.message }}</p>
              <p v-if="row.description" class="text-xs text-ink-subtle mt-0.5">{{ row.description }}</p>
            </template>
            <template #trigger="{ row }">
              <span :class="['badge', triggerInfo(row).cls]" :title="triggerInfo(row).detail">{{ triggerInfo(row).label }}</span>
              <p v-if="triggerInfo(row).detail" class="text-xs text-ink-subtle mt-1 font-mono">{{ triggerInfo(row).detail }}</p>
            </template>
            <template #next_run_at="{ value }"><span class="text-xs text-ink-muted">{{ fmtTime(value) }}</span></template>
            <template #status="{ value }">
              <span :class="['badge', statusBadge(value)]">{{ statusLabel(value) }}</span>
            </template>
            <template #actions="{ row }">
              <button v-if="row.status === 'pending'" class="btn btn-ghost text-xs text-danger"
                      @click="cancelTask(row)" :aria-label="'取消任务 ' + row.id">✕ 取消</button>
              <span v-else class="text-xs text-ink-subtle">—</span>
            </template>
          </base-table>
          <div v-if="total > pageSize" class="mt-4 flex justify-center">
            <pagination :page="page" :total="total" :page-size="pageSize" @change="onPageChange" />
          </div>
        </base-card>
      </div>
    `
  }
})()
