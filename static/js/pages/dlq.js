/* 死信队列管理（DLQ）— 列表 + 重试/放弃
 * 覆盖后端 GET /schedules/dlq（列表）、POST /schedules/dlq/{task_id}/retry（重试）、POST /schedules/dlq/{task_id}/cancel（放弃）
 * DLQ 条目：retry_count >= max_retries 的 failed 任务
 */
(function () {
  const { onMounted, computed } = Vue
  window.Pages = window.Pages || {}

  window.Pages.Dlq = {
    name: 'DlqPage',
    setup() {
      const state = window.AppState
      // useListPage 统一分页加载/翻页（消除重复样板）
      const lp = window.useListPage('/schedules/dlq', { failMsg: '加载死信队列失败', withKeyword: false })
      const { list, total, page, pageSize, loading, loadList, onPageChange } = lp

      const columns = computed(() => [
        { key: 'id', label: 'ID', width: '60px' },
        { key: 'message', label: '触发消息' },
        { key: 'retry_count', label: '重试', width: '80px' },
        { key: 'last_error', label: '最后错误' },
        { key: 'last_run_at', label: '最后执行', width: '150px' },
        { key: 'actions', label: '操作', width: '140px', align: 'center' }
      ])

      const retryTask = async (row) => {
        const act = await state.confirmAction({
          title: '重试死信任务',
          message: '确认重试任务 #' + row.id + '？将重置重试计数并立即入队执行。',
          confirmText: '重试'
        })
        if (act !== 'confirm') return
        try {
          await window.API.post('/schedules/dlq/' + row.id + '/retry')
          state.notify('任务已重新入队', 'success')
          loadList()
        } catch (e) { state.notify('重试失败：' + (e.message || ''), 'error') }
      }

      const cancelTask = async (row) => {
        const act = await state.confirmAction({
          title: '放弃死信任务', message: '确认放弃任务 #' + row.id + '？将永久取消，不可恢复。',
          confirmText: '放弃', danger: true
        })
        if (act !== 'confirm') return
        try {
          await window.API.post('/schedules/dlq/' + row.id + '/cancel')
          state.notify('任务已放弃', 'success')
          loadList()
        } catch (e) { state.notify('操作失败：' + (e.message || ''), 'error') }
      }

      const fmtTime = window.Format.time
      const goSchedules = () => state.navigate('/schedules')

      onMounted(() => loadList())

      return {
        list, total, page, pageSize, loading, columns,
        loadList, retryTask, cancelTask, onPageChange,
        fmtTime, goSchedules, state
      }
    },
    template: `
      <div class="space-y-4">
        <base-card>
          <div class="flex flex-wrap items-center gap-3">
            <div>
              <h2 class="text-lg font-semibold text-ink">死信队列（DLQ）</h2>
              <p class="text-xs text-ink-subtle mt-0.5">重试次数耗尽的失败任务。可手动重试或放弃。</p>
            </div>
            <div class="ml-auto flex gap-2">
              <button class="btn btn-secondary text-sm" @click="loadList" title="刷新">🔄 刷新</button>
              <button class="btn btn-ghost text-sm" @click="goSchedules" title="返回定时任务">← 定时任务</button>
            </div>
          </div>
        </base-card>

        <base-card>
          <base-table :columns="columns" :rows="list" :loading="loading"
                      empty="暂无死信任务。所有定时任务均执行正常。">
            <template #id="{ value }"><span class="text-xs text-ink-subtle">#{{ value }}</span></template>
            <template #message="{ row }">
              <p class="text-sm text-ink line-clamp-2" :title="row.message">{{ row.message }}</p>
              <p v-if="row.cron_expr" class="text-xs text-ink-subtle mt-0.5 font-mono">cron: {{ row.cron_expr }}</p>
            </template>
            <template #retry_count="{ row }">
              <span class="text-sm text-danger font-medium">{{ row.retry_count }}/{{ row.max_retries }}</span>
            </template>
            <template #last_error="{ value }">
              <p v-if="value" class="text-xs text-danger line-clamp-2 font-mono" :title="value">{{ value }}</p>
              <span v-else class="text-xs text-ink-subtle">—</span>
            </template>
            <template #last_run_at="{ value }"><span class="text-xs text-ink-muted">{{ fmtTime(value) }}</span></template>
            <template #actions="{ row }">
              <div class="flex gap-1 justify-center">
                <button class="btn btn-secondary text-xs" @click="retryTask(row)" :aria-label="'重试任务 ' + row.id">🔄 重试</button>
                <button class="btn btn-ghost text-xs text-danger" @click="cancelTask(row)" :aria-label="'放弃任务 ' + row.id">✕ 放弃</button>
              </div>
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
