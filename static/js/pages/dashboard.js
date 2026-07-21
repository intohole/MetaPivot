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
      const templates = ref([])
      const instantiating = ref('')
      const loading = ref(true)
      // P2-6: 企业管理概览（仅管理端可见）
      const mgmtStats = ref(null)

      const statCards = computed(() => [
        { key: 'tasks', label: 'Agent 任务', value: stats.value.tasks, icon: '🤖' },
        { key: 'skills', label: '可用 Skill', value: stats.value.skills, icon: '🧩' },
        { key: 'workflows', label: '工作流', value: stats.value.workflows, icon: '⚡' },
        { key: 'todayCalls', label: '今日调用', value: stats.value.todayCalls, icon: '📈' }
      ])

      // P2-6: 管理端概览卡片（仅 manager+ 角色有数据）
      const mgmtCards = computed(() => {
        if (!mgmtStats.value) return []
        const m = mgmtStats.value
        return [
          { label: '企业用户', value: m.user_count, sub: m.active_users + ' 活跃', icon: '👥' },
          { label: '定时任务', value: m.schedule_count, icon: '⏰' },
          { label: 'Agent 任务', value: m.agent_task_count, icon: '🤖' },
          { label: '今日调用', value: m.today_calls, icon: '📊' },
        ]
      })

      const loadDashboard = async () => {
        loading.value = true
        try {
          // 并行加载各项数据
          const [tasksRes, skillsRes, workflowsRes, auditRes, tplRes] = await Promise.allSettled([
            window.API.get('/agent/tasks', { page: 1, page_size: 5 }),
            window.API.get('/skills', { page: 1, page_size: 1 }),
            window.API.get('/workflows', { page: 1, page_size: 1 }),
            state.hasRole('admin') || state.hasRole('manager')
              ? window.API.get('/audit/stats', { group_by: 'day' }) : Promise.resolve(null),
            window.API.get('/workflows/templates', { page: 1, page_size: 4 }),
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
          if (tplRes.status === 'fulfilled' && tplRes.value) {
            templates.value = (tplRes.value.items || []).slice(0, 4)
          }
          // P2-6: 管理端概览（仅 manager+ 加载，非管理端 403 静默忽略）
          if (state.hasRole('manager')) {
            try {
              mgmtStats.value = await window.API.get('/overview')
            } catch (_) { /* 非管理端无权限，忽略 */ }
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

      // Round 4: 新手引导（手动触发 + 首次访问自动触发）
      const startTour = () => {
        if (window.startTour) window.startTour()
      }

      // 新手引导步骤（stats.tasks === 0 时显示）
      const onboardingSteps = [
        { step: 1, title: '配置 LLM API Key', desc: '在 .env 填入 LLM_API_KEY（支持 Kimi/Qwen/GLM）', path: '/configs' },
        { step: 2, title: '测试 Agent 对话', desc: '发起一次对话，验证 LLM + Agent 状态机正常', path: '/agent' },
        { step: 3, title: '注册 Skill 能力', desc: '添加业务 Skill 或接入 MCP Server', path: '/skills' },
        { step: 4, title: '接入 IM 渠道', desc: '配置钉钉/企微/飞书 webhook', path: '/channels' }
      ]
      const showOnboarding = computed(() => !loading.value && stats.value.tasks === 0)

      // Sprint 6.2: 一键实例化模板（快速上手自动化）
      const instantiateTemplate = async (tpl) => {
        if (instantiating.value) return
        instantiating.value = tpl.id
        try {
          const res = await window.API.post('/workflows/templates/' + tpl.id + '/instantiate', {
            name: '', trigger_overrides: { type: (tpl.trigger_template || {}).type || 'manual' },
          })
          state.notify('已创建工作流：' + (res.name || tpl.name) + '，可前往配置触发器', 'success')
          state.navigate('/workflows')
        } catch (e) {
          state.notify('创建失败：' + (e.message || '未知错误'), 'error')
        } finally { instantiating.value = '' }
      }

      onMounted(async () => {
        await loadDashboard()
        // Round 4: 首次访问且无任务时，延迟 800ms 自动触发新手引导（等数据加载完）
        if (!localStorage.getItem('metapivot_tour_done') && stats.value.tasks === 0) {
          setTimeout(() => { if (window.startTour) window.startTour() }, 800)
        }
      })

      return {
        stats, statCards, recentTasks, templates, instantiating, loading,
        quickActions, onboardingSteps, showOnboarding, startTour,
        instantiateTemplate, state, loadDashboard,
        mgmtStats, mgmtCards,
      }
    },
    template: `
      <div class="space-y-6">
        <!-- 统计卡片（Linear 风格：大数字 + uppercase label + 品牌色图标）-->
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <div v-for="c in statCards" :key="c.key" class="card p-5 hover:border-strong transition-colors">
            <div class="flex items-center justify-between">
              <div>
                <p class="text-xs font-medium text-ink-subtle uppercase tracking-wider">{{ c.label }}</p>
                <p class="mt-2 text-3xl font-semibold text-ink" style="letter-spacing: -0.02em;">{{ c.value }}</p>
              </div>
              <div class="w-10 h-10 rounded-lg bg-brand-light flex items-center justify-center text-xl" aria-hidden="true">{{ c.icon }}</div>
            </div>
          </div>
        </div>

        <!-- P2-6: 企业管理概览（仅管理端 manager+ 可见） -->
        <base-card v-if="mgmtCards.length > 0" title="🏢 企业管理概览" subtitle="当前租户资源用量与活跃度">
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <div v-for="c in mgmtCards" :key="c.label" class="p-4 rounded-lg border border-border hover:border-brand transition-colors">
              <div class="flex items-center gap-2 mb-2">
                <span class="text-lg" aria-hidden="true">{{ c.icon }}</span>
                <p class="text-xs font-medium text-ink-subtle uppercase tracking-wider">{{ c.label }}</p>
              </div>
              <p class="text-2xl font-semibold text-ink" style="letter-spacing: -0.02em;">{{ c.value }}</p>
              <p v-if="c.sub" class="text-xs text-ink-muted mt-1">{{ c.sub }}</p>
            </div>
          </div>
        </base-card>

        <!-- 新手引导（无任务时显示） -->
        <base-card v-if="showOnboarding" title="🚀 快速开始指南" subtitle="按步骤完成初始化，4 步上手 MetaPivot">
          <div class="space-y-3">
            <div v-for="s in onboardingSteps" :key="s.step"
                 class="flex items-start gap-3 p-3 rounded-lg hover:bg-brand-light transition-colors cursor-pointer"
                 @click="state.navigate(s.path)">
              <div class="flex-shrink-0 w-8 h-8 rounded-full bg-brand text-white flex items-center justify-center text-sm font-bold"
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
        <base-card title="快捷操作" data-tour="quick-actions">
          <template #action>
            <button class="btn btn-secondary text-sm" @click="startTour" aria-label="启动新手引导">🎓 新手引导</button>
          </template>
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <button v-for="a in quickActions" :key="a.path"
                    @click="state.navigate(a.path)"
                    class="card p-4 hover:shadow-md hover:border-brand transition-all text-center group">
              <div class="text-3xl mb-2 group-hover:scale-110 transition-transform" aria-hidden="true">{{ a.icon }}</div>
              <p class="text-sm font-medium text-ink">{{ a.label }}</p>
            </button>
          </div>
        </base-card>

        <!-- Sprint 6.2: 常用自动化模板（一键上手） -->
        <base-card v-if="templates.length > 0" title="⚡ 常用自动化模板" subtitle="一键实例化为工作流，快速上手自动化">
          <template #action>
            <button class="btn btn-secondary text-sm" @click="state.navigate('/templates')">查看全部 →</button>
          </template>
          <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <div v-for="tpl in templates" :key="tpl.id"
                 class="card p-4 hover:shadow-md hover:border-brand transition-all flex flex-col group">
              <div class="flex items-start justify-between mb-2">
                <div class="text-2xl" aria-hidden="true">{{ tpl.category === 'daily' ? '☀️' : tpl.category === 'weekly' ? '📆' : tpl.category === 'report' ? '📄' : tpl.category === 'notification' ? '🔔' : tpl.category === 'communication' ? '💬' : '⚙️' }}</div>
                <span v-if="tpl.usage_count > 0" class="text-xs text-ink-subtle">用过 {{ tpl.usage_count }} 次</span>
              </div>
              <p class="font-medium text-ink group-hover:text-brand text-sm">{{ tpl.name }}</p>
              <p class="text-xs text-ink-muted mt-1 flex-1 line-clamp-2">{{ tpl.description || '无描述' }}</p>
              <button class="btn btn-primary text-xs mt-3 w-full"
                      :disabled="instantiating === tpl.id"
                      @click="instantiateTemplate(tpl)"
                      :aria-label="'一键创建工作流：' + tpl.name">
                <span v-if="instantiating === tpl.id">创建中…</span>
                <span v-else>⚡ 一键创建</span>
              </button>
            </div>
          </div>
        </base-card>

        <!-- 最近任务 -->
        <base-card title="最近 Agent 任务" subtitle="最近 5 条任务执行记录">
          <template #action>
            <button class="btn btn-secondary text-sm" @click="state.navigate('/agent')">查看全部 →</button>
          </template>
          <table-skeleton v-if="loading" :rows="5" :cols="3" />
          <base-table v-else :columns="[
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
