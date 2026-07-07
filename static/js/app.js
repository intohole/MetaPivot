/* ============================================================
   应用主入口 — Vue 应用 + 布局 + 路由
   页面组件在各自文件中注册到 window.Pages，本文件最后加载
   Round 4: 主题切换按钮 + 全局 ConfirmDialog 挂载 + Skeleton 注册
   ============================================================ */
(function () {
  const { createApp, computed, defineComponent, watch, ref, onMounted, onUnmounted } = Vue
  const state = window.AppState

  /* === 预注册命令（供 Command Palette 消费）=== */
  if (window.Commands) {
    const navCmds = [
      { id: 'nav-dashboard', label: '仪表盘', icon: '📊', path: '/dashboard', keywords: 'dashboard home 首页', group: 'navigation', shortcut: 'g d' },
      { id: 'nav-agent', label: 'Agent 任务', icon: '🤖', path: '/agent', keywords: 'agent 任务 对话', group: 'navigation', shortcut: 'g a' },
      { id: 'nav-skills', label: 'Skill 管理', icon: '🧩', path: '/skills', keywords: 'skill 技能 mcp', group: 'navigation', shortcut: 'g s' },
      { id: 'nav-workflows', label: '工作流', icon: '⚡', path: '/workflows', keywords: 'workflow 工作流 flow', group: 'navigation', shortcut: 'g w' },
      { id: 'nav-knowledge', label: '知识库', icon: '📚', path: '/knowledge', keywords: 'knowledge 知识 文档', group: 'navigation', shortcut: 'g k' },
      { id: 'nav-channels', label: 'IM 渠道', icon: '💬', path: '/channels', keywords: 'channel 渠道 钉钉 企微 飞书', group: 'navigation', shortcut: 'g c' }
    ]
    navCmds.forEach(c => window.Commands.register(c))
    // 动作命令（按角色过滤在执行时处理）
    window.Commands.register({
      id: 'action-logout', label: '退出登录', icon: '🚪', keywords: 'logout exit 退出', group: 'actions',
      action: () => {
        const s = window.AppState
        s.confirmAction({ title: '退出登录', message: '确认退出登录？', confirmText: '退出', danger: true })
          .then(act => { if (act === 'confirm') s.logout() })
      }
    })
    window.Commands.register({
      id: 'action-theme', label: '切换主题', icon: '🌙', keywords: 'theme dark light 主题 暗色', group: 'actions',
      action: () => window.AppState.toggleTheme()
    })
    // Phase 1 Bundle A：全局中枢动作命令（参数化命令用 inputPrompt 标记）
    window.Commands.registerActions([
      {
        id: 'action-agent-task', label: '发起 Agent 任务', icon: '🤖', keywords: 'agent task 对话 发起 ask', group: 'actions',
        inputPrompt: '输入 Agent 任务消息...',
        action: (msg) => { window.AppState.pendingMessage = msg; window.AppState.navigate('/agent') }
      },
      {
        id: 'action-create-skill', label: '新建 Skill', icon: '🧩', keywords: 'create skill 新建 创建 技能', group: 'actions',
        action: () => { window.AppState.pendingAction = 'create-skill'; window.AppState.navigate('/skills') }
      },
      {
        id: 'action-create-workflow', label: '新建工作流', icon: '⚡', keywords: 'create workflow 新建 创建 工作流', group: 'actions',
        action: () => { window.AppState.pendingAction = 'create-workflow'; window.AppState.navigate('/workflows') }
      },
      {
        id: 'action-create-knowledge', label: '上传知识文档', icon: '📚', keywords: 'create knowledge upload 上传 知识 文档', group: 'actions',
        action: () => { window.AppState.pendingAction = 'create-knowledge'; window.AppState.navigate('/knowledge') }
      },
      {
        id: 'action-query-knowledge', label: '查询知识库', icon: '🔍', keywords: 'search knowledge query 查询 检索 知识', group: 'actions',
        inputPrompt: '输入知识查询关键词...',
        action: (q) => { window.AppState.pendingQuery = q; window.AppState.navigate('/knowledge') }
      },
      {
        id: 'action-create-webhook', label: '新建 Webhook', icon: '🔗', keywords: 'create webhook 新建 触发器', group: 'actions',
        action: () => { window.AppState.pendingAction = 'create-webhook'; window.AppState.navigate('/webhooks') }
      },
      {
        id: 'action-save-last-task-as-skill', label: '保存最近任务为 Skill', icon: '💾',
        keywords: 'save skill task 录制 沉淀', group: 'actions',
        action: () => { window.AppState.pendingAction = 'save-last-task-as-skill'; window.AppState.navigate('/agent') }
      }
    ])
  }

  /* === 路由配置 === */
  const ROUTES = [
    { path: '/login', label: '登录', component: 'LoginPage', icon: '🔑', hideNav: true },
    { path: '/dashboard', label: '仪表盘', component: 'DashboardPage', icon: '📊' },
    { path: '/agent', label: 'Agent 任务', component: 'AgentPage', icon: '🤖' },
    { path: '/skills', label: 'Skill 管理', component: 'SkillsPage', icon: '🧩' },
    { path: '/skill-review', label: '自进化 Review', component: 'SkillReviewPage', icon: '📝', hideNav: true },
    { path: '/workflows', label: '工作流', component: 'WorkflowsPage', icon: '⚡' },
    { path: '/webhooks', label: 'Webhook', component: 'WebhooksPage', icon: '🔗', roles: ['admin'] },
    { path: '/knowledge', label: '知识库', component: 'KnowledgePage', icon: '📚' },
    { path: '/audit', label: '审计日志', component: 'AuditPage', icon: '📋', roles: ['admin', 'manager'] },
    { path: '/users', label: '用户管理', component: 'UsersPage', icon: '👥', roles: ['admin'] },
    { path: '/channels', label: 'IM 渠道', component: 'ChannelsPage', icon: '💬' },
    { path: '/configs', label: '系统配置', component: 'ConfigsPage', icon: '⚙️', roles: ['admin'] }
  ]

  const currentRoute = computed(() => {
    const p = state.currentRoute.value
    return ROUTES.find(r => r.path === p) || ROUTES[1]
  })

  const visibleNav = computed(() => {
    return ROUTES.filter(r => {
      if (r.hideNav) return false
      if (!r.roles) return true
      return r.roles.some(role => state.hasRole(role))
    })
  })

  /* === 路由守卫 === */
  function guardRoute() {
    const path = state.currentRoute.value
    const route = ROUTES.find(r => r.path === path)
    if (!state.user.value && path !== '/login') { state.navigate('/login'); return }
    if (state.user.value && path === '/login') { state.navigate('/dashboard'); return }
    if (route?.roles && !route.roles.some(r => state.hasRole(r))) {
      state.notify('无权访问该页面', 'warning')
      state.navigate('/dashboard')
    }
  }

  /* === 应用根组件 === */
  const App = defineComponent({
    name: 'App',
    setup() {
      watch(state.currentRoute, guardRoute, { immediate: true })
      const sidebarOpen = ref(true)
      const toggleSidebar = () => { sidebarOpen.value = !sidebarOpen.value }
      // Round 4: handleLogout 改用全局 confirmAction 替代原生 confirm()
      const handleLogout = async () => {
        const action = await state.confirmAction({
          title: '退出登录', message: '确认退出登录？', confirmText: '退出', danger: true
        })
        if (action === 'confirm') state.logout()
      }
      // Phase 2: 命令面板 ⌘K / Ctrl+K 全局快捷键（focus trap 阻断 Modal 内触发）
      const paletteRef = ref(null)
      const onGlobalKeydown = (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
          e.preventDefault()
          if (paletteRef.value && paletteRef.value.open) paletteRef.value.open()
        }
      }
      onMounted(() => document.addEventListener('keydown', onGlobalKeydown))
      onUnmounted(() => document.removeEventListener('keydown', onGlobalKeydown))
      return { state, currentRoute, visibleNav, sidebarOpen, toggleSidebar, handleLogout, paletteRef }
    },
    template: `
      <div>
        <loading-bar />
        <toast-container />

        <login-page v-if="currentRoute.path === '/login'" />

        <div v-else class="flex min-h-screen">
          <aside :class="['bg-surface border-r border-border transition-all duration-200 flex flex-col', sidebarOpen ? 'w-60' : 'w-0 overflow-hidden']"
                 aria-label="主导航" data-tour="sidebar">
            <div class="flex items-center gap-2 px-5 h-16 border-b border-border">
              <span class="text-2xl" aria-hidden="true">🤖</span>
              <span class="font-bold text-ink">MetaPivot</span>
            </div>
            <nav class="flex-1 py-4 px-2 space-y-1" role="navigation">
              <button v-for="r in visibleNav" :key="r.path"
                      @click="state.navigate(r.path)"
                      :class="['w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors',
                               currentRoute.path === r.path ? 'bg-brand-light text-brand' : 'text-ink-muted hover:bg-surface-muted hover:text-ink']"
                      :aria-current="currentRoute.path === r.path ? 'page' : undefined">
                <span aria-hidden="true" class="text-base">{{ r.icon }}</span>
                <span>{{ r.label }}</span>
              </button>
            </nav>
            <div class="px-2 py-3 border-t border-border">
              <div class="px-3 py-2 text-xs text-ink-subtle" v-if="state.user.value">
                <p class="font-medium text-ink truncate">{{ state.user.value.username }}</p>
                <p class="mt-0.5">{{ state.user.value.role }}</p>
              </div>
              <button class="btn btn-ghost w-full justify-start text-sm" @click="handleLogout" aria-label="退出登录">
                <span aria-hidden="true">🚪</span> 退出登录
              </button>
            </div>
          </aside>

          <div class="flex-1 flex flex-col min-w-0">
            <header class="bg-surface border-b border-border h-16 flex items-center justify-between px-6 sticky top-0 z-30" data-tour="header">
              <div class="flex items-center gap-3">
                <button @click="toggleSidebar" class="btn btn-ghost p-2" :aria-label="sidebarOpen ? '收起侧边栏' : '展开侧边栏'" :aria-expanded="sidebarOpen">
                  <span aria-hidden="true">☰</span>
                </button>
                <h1 class="text-base font-semibold text-ink">{{ currentRoute.label }}</h1>
              </div>
              <div class="flex items-center gap-2 text-sm text-ink-muted">
                <button class="btn btn-ghost p-2" @click="state.toggleTheme" :aria-label="state.theme.value === 'light' ? '切换暗色模式' : '切换亮色模式'" title="切换主题">
                  <span aria-hidden="true">{{ state.theme.value === 'light' ? '🌙' : '☀️' }}</span>
                </button>
                <button class="btn btn-ghost p-2 hidden sm:inline-flex" @click="paletteRef && paletteRef.open()" aria-label="打开命令面板" title="命令面板（⌘K / Ctrl+K）" data-tour="palette-hint">
                  <span aria-hidden="true">⌘K</span>
                </button>
                <span class="badge badge-success"><span class="w-1.5 h-1.5 rounded-full bg-current" aria-hidden="true"></span>系统正常</span>
              </div>
            </header>

            <main class="flex-1 p-6 bg-surface-muted overflow-y-auto" role="main">
              <component :is="currentRoute.component" />
            </main>
          </div>
        </div>

        <!-- Round 4: 全局 ConfirmDialog（业务页面调 state.confirmAction） -->
        <confirm-dialog :model-value="state.confirmState.visible" :title="state.confirmState.title" :message="state.confirmState.message" :confirm-text="state.confirmState.confirmText" :danger="state.confirmState.danger" @update:model-value="v => { if (!v) state.resolveConfirm('cancel') }" @confirm="state.resolveConfirm('confirm')" @cancel="state.resolveConfirm('cancel')" />
        <!-- Phase 2: 命令面板（⌘K / Ctrl+K 唤起） -->
        <command-palette ref="paletteRef" />
      </div>
    `
  })

  /* === 创建应用并注册组件 === */
  const app = createApp(App)
  const C = window.Components
  app.component('LoadingBar', C.LoadingBar)
  app.component('ToastContainer', C.ToastContainer)
  app.component('BaseCard', C.BaseCard)
  app.component('EmptyState', C.EmptyState)
  app.component('StatusBadge', C.StatusBadge)
  app.component('Pagination', C.Pagination)
  app.component('BaseTable', C.BaseTable)
  app.component('BaseModal', C.BaseModal)
  app.component('ConfirmDialog', C.ConfirmDialog)
  app.component('FormField', C.FormField)
  app.component('Skeleton', C.Skeleton)
  app.component('TableSkeleton', C.TableSkeleton)
  // Phase 2: 新增交互组件
  app.component('CommandPalette', C.CommandPalette)
  app.component('DropdownMenu', C.DropdownMenu)
  app.component('Tooltip', C.Tooltip)
  app.component('Tabs', C.Tabs)
  app.component('Breadcrumb', C.Breadcrumb)
  // Phase 3: 表单 + 抽屉组件
  app.component('Switch', C.Switch)
  app.component('TagInput', C.TagInput)
  app.component('Drawer', C.Drawer)
  // Phase 1 Bundle A: Agent 执行轨迹组件（消除 agent 黑盒）
  app.component('AgentTrace', C.AgentTrace)

  // 注册页面组件（pages/*.js 已先于本文件执行，挂载到 window.Pages）
  const P = window.Pages || {}
  const pageMap = {
    'LoginPage': P.Login, 'DashboardPage': P.Dashboard, 'AgentPage': P.Agent,
    'SkillsPage': P.Skills, 'SkillReviewPage': P.SkillReview, 'WorkflowsPage': P.Workflows, 'KnowledgePage': P.Knowledge,
    'AuditPage': P.Audit, 'UsersPage': P.Users, 'ChannelsPage': P.Channels,
    'ConfigsPage': P.Configs, 'WebhooksPage': P.Webhooks
  }
  for (const [name, def] of Object.entries(pageMap)) {
    app.component(name, def || { template: '<div class="p-6 text-ink-muted">页面加载失败：' + name + '</div>' })
  }

  app.mount('#app')
  console.log('MetaPivot 管理后台已启动')
})()
