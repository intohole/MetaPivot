/* Skill 自进化 Review 页 — 草稿审批 + 修订审批（PR-like workflow）
   从 Skills 页 "📝 自进化 Review" 按钮进入。
   能力：
   1. 草稿队列：reflector/failure_analyzer 自动生成的待审核 Skill，approve→转正式 / reject
   2. 修订队列：optimizer 自动生成的 Skill 版本修订，approve→应用 / reject
   3. 健康度视图：按失败率标记 healthy/degraded/critical
*/
(function () {
  const { ref, reactive, onMounted, computed } = Vue
  window.Pages = window.Pages || {}

  window.Pages.SkillReview = {
    name: 'SkillReviewPage',
    setup() {
      const state = window.AppState
      const tab = ref('drafts') // drafts | revisions
      const loading = ref(false)
      const list = ref([])
      const total = ref(0)
      const page = ref(1)
      const pageSize = ref(20)
      const statusFilter = ref('pending')
      const showDetail = ref(false)
      const detailItem = ref(null)
      const detailType = ref('draft') // draft | revision

      const isAdmin = computed(() => state.hasRole('admin') || state.hasRole('manager'))

      const draftCols = [
        { key: 'name', label: '名称' },
        { key: 'origin', label: '来源', width: '120px' },
        { key: 'confidence', label: '置信度', width: '90px' },
        { key: 'status', label: '状态', width: '90px' },
        { key: 'created_at', label: '生成时间', width: '160px' },
        { key: 'actions', label: '操作', width: '160px', align: 'center' }
      ]
      const revCols = [
        { key: 'diff_summary', label: '变更摘要' },
        { key: 'source', label: '来源', width: '110px' },
        { key: 'confidence', label: '置信度', width: '90px' },
        { key: 'status', label: '状态', width: '100px' },
        { key: 'created_at', label: '时间', width: '160px' },
        { key: 'actions', label: '操作', width: '160px', align: 'center' }
      ]

      const loadList = async () => {
        loading.value = true
        try {
          const path = tab.value === 'drafts' ? '/skills/drafts/list' : '/skills/revisions/list'
          const params = { page: page.value, page_size: pageSize.value, status: statusFilter.value }
          const res = await window.API.get(path, params)
          list.value = res.items || []
          total.value = res.total || 0
        } catch (e) {
          state.notify('加载失败：' + (e.message || ''), 'error')
        } finally { loading.value = false }
      }

      const onTabChange = (t) => { tab.value = t; page.value = 1; statusFilter.value = 'pending'; loadList() }
      const onStatusChange = (s) => { statusFilter.value = s; page.value = 1; loadList() }
      const onPageChange = ({ page: p }) => { page.value = p; loadList() }

      const viewDetail = (row) => {
        detailItem.value = row
        detailType.value = tab.value === 'drafts' ? 'draft' : 'revision'
        showDetail.value = true
      }

      const approve = async (row) => {
        const act = await state.confirmAction({
          title: '确认批准', message: tab.value === 'drafts'
            ? '批准后将创建正式 Skill：' + row.name
            : '批准后将应用修订到 Skill（版本 → v' + row.version + '）',
          confirmText: '批准'
        })
        if (act !== 'confirm') return
        try {
          const path = tab.value === 'drafts'
            ? '/skills/drafts/' + row.id + '/approve'
            : '/skills/revisions/' + row.id + '/approve'
          const res = await window.API.post(path)
          state.notify(tab.value === 'drafts' ? '已批准，Skill ID: ' + (res.skill_id || '').slice(0, 8) : '修订已应用', 'success')
          loadList()
        } catch (e) { state.notify('操作失败：' + (e.message || ''), 'error') }
      }

      const reject = async (row) => {
        const act = await state.confirmAction({ title: '确认拒绝', message: '拒绝此' + (tab.value === 'drafts' ? '草稿' : '修订') + '？', confirmText: '拒绝', danger: true })
        if (act !== 'confirm') return
        try {
          const path = tab.value === 'drafts'
            ? '/skills/drafts/' + row.id + '/reject'
            : '/skills/revisions/' + row.id + '/reject'
          await window.API.post(path)
          state.notify('已拒绝', 'success')
          loadList()
        } catch (e) { state.notify('操作失败：' + (e.message || ''), 'error') }
      }

      const originLabel = (o) => ({
        reflector: '🔁 经验固化', failure_analyzer: '⚠️ 失败分析', manual: '✋ 手动'
      }[o] || o)
      const statusBadge = (s) => ({
        pending: 'badge-warning', approved: 'badge-success',
        rejected: 'badge-muted', auto_merged: 'badge-info'
      }[s] || 'badge-muted')
      const statusLabel = (s) => ({ pending: '待审核', approved: '已批准', rejected: '已拒绝', auto_merged: '自动合并' }[s] || s)
      const confColor = (c) => c >= 0.8 ? 'text-success' : c >= 0.5 ? 'text-warning' : 'text-ink-muted'
      const fmtTime = (t) => t ? new Date(t).toLocaleString('zh-CN', { hour12: false }) : '-'
      const goSkills = () => state.navigate('/skills')

      onMounted(() => loadList())

      return {
        tab, loading, list, total, page, pageSize, statusFilter, showDetail, detailItem, detailType,
        isAdmin, draftCols, revCols,
        loadList, onTabChange, onStatusChange, onPageChange, viewDetail, approve, reject,
        originLabel, statusBadge, statusLabel, confColor, fmtTime, goSkills, state
      }
    },
    template: `
      <div class="space-y-4">
        <base-card>
          <div class="flex flex-wrap items-center gap-3">
            <div class="flex gap-1">
              <button :class="['btn text-sm', tab==='drafts'?'btn-primary':'btn-ghost']" @click="onTabChange('drafts')">📝 草稿 Review</button>
              <button :class="['btn text-sm', tab==='revisions'?'btn-primary':'btn-ghost']" @click="onTabChange('revisions')">🔄 修订 Review</button>
            </div>
            <div class="flex gap-1 ml-2">
              <button v-for="s in ['pending','approved','rejected']" :key="s"
                :class="['btn text-xs', statusFilter===s?'btn-secondary':'btn-ghost']"
                @click="onStatusChange(s)">{{ s==='pending'?'待审核':s==='approved'?'已批准':'已拒绝' }}</button>
              <button v-if="tab==='revisions'" :class="['btn text-xs', statusFilter==='auto_merged'?'btn-secondary':'btn-ghost']" @click="onStatusChange('auto_merged')">自动合并</button>
            </div>
            <button class="btn btn-ghost text-sm ml-auto" @click="goSkills">← 返回 Skill 管理</button>
          </div>
        </base-card>

        <base-card>
          <base-table :columns="tab==='drafts'?draftCols:revCols" :rows="list" :loading="loading">
            <template #name="{ row }">
              <p class="font-medium text-ink">{{ row.name }}</p>
              <p class="text-xs text-ink-muted truncate max-w-xs">{{ row.description }}</p>
            </template>
            <template #origin="{ value }"><span class="badge badge-info">{{ originLabel(value) }}</span></template>
            <template #diff_summary="{ row }">
              <p class="text-sm text-ink">{{ row.diff_summary }}</p>
              <p class="text-xs text-ink-muted">v{{ row.version }} · {{ row.reasoning?.slice(0,60) || '' }}{{ row.reasoning?.length>60?'…':'' }}</p>
            </template>
            <template #source="{ value }">
              <span class="badge badge-muted">{{ value==='auto_optimize'?'自动优化':value==='failure_analysis'?'失败分析':value }}</span>
            </template>
            <template #confidence="{ value }">
              <span :class="['font-mono text-sm font-semibold', confColor(value)]">{{ (value*100).toFixed(0) }}%</span>
            </template>
            <template #status="{ value }">
              <span :class="['badge', statusBadge(value)]">{{ statusLabel(value) }}</span>
            </template>
            <template #created_at="{ value }"><span class="text-xs text-ink-muted">{{ fmtTime(value) }}</span></template>
            <template #actions="{ row }">
              <div class="flex gap-1 justify-center">
                <button class="btn btn-ghost text-xs" @click="viewDetail(row)" title="详情">👁️</button>
                <template v-if="isAdmin && row.status==='pending'">
                  <button class="btn btn-ghost text-xs text-success" @click="approve(row)" title="批准">✓</button>
                  <button class="btn btn-ghost text-xs text-danger" @click="reject(row)" title="拒绝">✕</button>
                </template>
              </div>
            </template>
          </base-table>
          <empty-state v-if="!loading && list.length===0" icon="📭" title="暂无数据" description="当前筛选条件下没有记录" />
          <pagination :page="page" :page-size="pageSize" :total="total" @change="onPageChange" />
        </base-card>

        <base-modal v-model="showDetail" :title="detailType==='draft'?'草稿详情':'修订详情'" width="max-w-2xl">
          <div v-if="detailItem" class="space-y-3 text-sm">
            <div class="grid grid-cols-2 gap-3">
              <div><span class="text-ink-muted">名称/版本：</span><span class="font-medium text-ink">{{ detailItem.name || 'v'+detailItem.version }}</span></div>
              <div><span class="text-ink-muted">状态：</span><span :class="['badge', statusBadge(detailItem.status)]">{{ statusLabel(detailItem.status) }}</span></div>
              <div><span class="text-ink-muted">置信度：</span><span :class="confColor(detailItem.confidence)">{{ (detailItem.confidence*100).toFixed(0) }}%</span></div>
              <div><span class="text-ink-muted">来源：</span>{{ detailType==='draft' ? originLabel(detailItem.origin) : detailItem.source }}</div>
              <div v-if="detailItem.task_id"><span class="text-ink-muted">来源任务：</span><span class="font-mono text-xs">{{ detailItem.task_id }}</span></div>
              <div><span class="text-ink-muted">生成时间：</span>{{ fmtTime(detailItem.created_at) }}</div>
            </div>
            <div v-if="detailItem.description"><span class="text-ink-muted">描述：</span>{{ detailItem.description }}</div>
            <div v-if="detailItem.reasoning"><span class="text-ink-muted">理由：</span>{{ detailItem.reasoning }}</div>
            <div v-if="detailItem.diff_summary"><span class="text-ink-muted">变更摘要：</span>{{ detailItem.diff_summary }}</div>
            <div v-if="detailType==='revision' && detailItem.new_definition">
              <p class="text-ink-muted mb-1">新定义：</p>
              <pre class="bg-surface-muted p-3 rounded-md text-xs font-mono overflow-x-auto max-h-60">{{ JSON.stringify(detailItem.new_definition, null, 2) }}</pre>
            </div>
            <div v-if="detailType==='draft' && detailItem.input_schema">
              <p class="text-ink-muted mb-1">输入 Schema：</p>
              <pre class="bg-surface-muted p-3 rounded-md text-xs font-mono overflow-x-auto max-h-40">{{ JSON.stringify(detailItem.input_schema, null, 2) }}</pre>
            </div>
          </div>
          <template #footer>
            <button class="btn btn-secondary" @click="showDetail=false">关闭</button>
            <template v-if="isAdmin && detailItem?.status==='pending'">
              <button class="btn btn-primary" @click="approve(detailItem); showDetail=false">批准</button>
              <button class="btn btn-danger" @click="reject(detailItem); showDetail=false">拒绝</button>
            </template>
          </template>
        </base-modal>
      </div>
    `
  }
})()
