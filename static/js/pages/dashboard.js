/* 仪表盘 — 系统概览 + 最近任务 + 快捷操作 */
(function () {
  const { ref, onMounted, computed } = Vue
  window.Pages = window.Pages || {}

  window.Pages.Dashboard = {
    name: 'DashboardPage',
    setup() {
      const state = window.AppState
      const stats = ref({ tasks: 0, skills: 0, workflows: 0, todayCalls: 0 })
      const recentTasks = ref([])
      const loading = ref(true)

      const statCards = computed(() => [
        { key: 'tasks', label: 'Agent 任务', value: stats.value.tasks, icon: '🤖', color: 'bg-blue-50 text-blue-700' },
        { key: 'skills', label: '可用 Skill', value: stats.value.skills, icon: '🧩', color: 'bg-purple-50 text-purple-700' },
        { key: 'workflows', label: '工作流', value: stats.value.workflows, icon: '⚡', color: 'bg-amber-50 text-amber-700' },
        { key: 'todayCalls', label: '今日调用', value: stats.value.todayCalls, icon: '📈', color: 'bg-green-50 text-green-700' }
      ])

      const loadDashboard = async () => {
        loading.value = true
        try {
          // 并行加载各项数据
          const [tasksRes, skillsRes, workflowsRes, auditRes] = await Promise.allSettled([
            window.API.get('/agent/tasks', { page: 1, page_size: 5 }),
            window.API.get('/skills', { page: 1, page_size: 1 }),
            window.API.get('/workflows', { page: 1, page_size: 1 }),
            state.hasRole('admin') || state.hasRole('manager')
              ? window.API.get('/audit/stats', { group_by: 'day' }) : Promise.resolve(null)
          ])
          // 任务列表
          if (tasksRes.status === 'fulfilled' && tasksRes.value) {
            recentTasks.value = tasksRes.value.items || []
            stats.value.tasks = tasksRes.value.total || 0
          }
          if (skillsRes.status === 'fulfilled' && skillsRes.value) stats.value.skills = skillsRes.value.total || 0
          if (workflowsRes.status === 'fulfilled' && workflowsRes.value) stats.value.workflows = workflowsRes.value.total || 0
          if (auditRes.status === 'fulfilled' && auditRes.value) {
            const today = new Date().toISOString().slice(0, 10)
            const todayStat = (auditRes.value.stats || []).find(s => s.key === today)
            stats.value.todayCalls = todayStat?.count || 0
          }
        } finally {
          loading.value = false
        }
      }

      const quickActions = [
        { label: '发起 Agent 对话', icon: '🤖', path: '/agent' },
        { label: '创建 Skill', icon: '🧩', path: '/skills' },
        { label: '配置工作流', icon: '⚡', path: '/workflows' },
        { label: '上传知识文档', icon: '📚', path: '/knowledge' }
      ]

      // 新手引导步骤（stats.tasks === 0 时显示）
      const onboardingSteps = [
        { step: 1, title: '配置 LLM API Key', desc: '在 .env 填入 LLM_API_KEY（支持 Kimi/Qwen/GLM）', path: '/configs' },
        { step: 2, title: '测试 Agent 对话', desc: '发起一次对话，验证 LLM + Agent 状态机正常', path: '/agent' },
        { step: 3, title: '注册 Skill 能力', desc: '添加业务 Skill 或接入 MCP Server', path: '/skills' },
        { step: 4, title: '接入 IM 渠道', desc: '配置钉钉/企微/飞书 webhook', path: '/channels' }
      ]
      const showOnboarding = computed(() => !loading.value && stats.value.tasks === 0)

      onMounted(loadDashboard)

      return { stats, statCards, recentTasks, loading, quickActions, onboardingSteps, showOnboarding, state, loadDashboard }
    },
    template: `
      <div class="space-y-6">
        <!-- 统计卡片 -->
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div v-for="c in statCards" :key="c.key" class="card p-5">
            <div class="flex items-center justify-between">
              <div>
                <p class="text-sm text-ink-muted">{{ c.label }}</p>
                <p class="mt-1 text-2xl font-bold text-ink">{{ c.value }}</p>
              </div>
              <div :class="['w-12 h-12 rounded-lg flex items-center justify-center text-2xl', c.color]" aria-hidden="true">{{ c.icon }}</div>
            </div>
          </div>
        </div>

        <!-- 新手引导（无任务时显示） -->
        <base-card v-if="showOnboarding" title="🚀 快速开始指南" subtitle="按步骤完成初始化，4 步上手 MetaPivot">
          <div class="space-y-3">
            <div v-for="s in onboardingSteps" :key="s.step"
                 class="flex items-start gap-3 p-3 rounded-lg hover:bg-blue-50 transition-colors cursor-pointer"
                 @click="state.navigate(s.path)">
              <div class="flex-shrink-0 w-8 h-8 rounded-full bg-blue-600 text-white flex items-center justify-center text-sm font-bold"
                   aria-hidden="true">{{ s.step }}</div>
              <div class="flex-1">
                <p class="text-sm font-medium text-ink">{{ s.title }}</p>
                <p class="text-xs text-ink-muted mt-0.5">{{ s.desc }}</p>
              </div>
              <div class="text-ink-subtle" aria-hidden="true">→</div>
            </div>
          </div>
        </base-card>

        <!-- 快捷操作 -->
        <base-card title="快捷操作">
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <button v-for="a in quickActions" :key="a.path"
                    @click="state.navigate(a.path)"
                    class="card p-4 hover:shadow-md hover:border-brand transition-all text-center group">
              <div class="text-3xl mb-2 group-hover:scale-110 transition-transform" aria-hidden="true">{{ a.icon }}</div>
              <p class="text-sm font-medium text-ink">{{ a.label }}</p>
            </button>
          </div>
        </base-card>

        <!-- 最近任务 -->
        <base-card title="最近 Agent 任务" subtitle="最近 5 条任务执行记录">
          <template #action>
            <button class="btn btn-secondary text-sm" @click="state.navigate('/agent')">查看全部 →</button>
          </template>
          <base-table :columns="[
            { key: 'task_id', label: '任务ID', width: '180px' },
            { key: 'status', label: '状态', width: '120px' },
            { key: 'created_at', label: '创建时间', width: '180px' }
          ]" :rows="recentTasks" :loading="loading" empty="暂无任务记录">
            <template #task_id="{ value }">
              <span class="font-mono text-xs text-ink-muted">{{ value?.slice(0, 8) }}...</span>
            </template>
            <template #status="{ value }">
              <status-badge :status="value" />
            </template>
            <template #created_at="{ value }">
              <span class="text-xs text-ink-muted">{{ value }}</span>
            </template>
          </base-table>
        </base-card>
      </div>
    `
  }
})()
