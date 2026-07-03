/* ============================================================
   应用主入口 — Vue 应用 + 布局 + 路由
   页面组件在各自文件中注册到 window.Pages，本文件最后加载
   ============================================================ */
(function () {
  const { createApp, computed, defineComponent, watch, ref } = Vue
  const state = window.AppState

  /* === 路由配置 === */
  const ROUTES = [
    { path: '/login', label: '登录', component: 'LoginPage', icon: '🔑', hideNav: true },
    { path: '/dashboard', label: '仪表盘', component: 'DashboardPage', icon: '📊' },
    { path: '/agent', label: 'Agent 任务', component: 'AgentPage', icon: '🤖' },
    { path: '/skills', label: 'Skill 管理', component: 'SkillsPage', icon: '🧩' },
    { path: '/workflows', label: '工作流', component: 'WorkflowsPage', icon: '⚡' },
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
      const handleLogout = () => { if (confirm('确认退出登录？')) state.logout() }
      return { state, currentRoute, visibleNav, sidebarOpen, toggleSidebar, handleLogout }
    },
    template: `
      <div>
        <loading-bar />
        <toast-container />

        <login-page v-if="currentRoute.path === '/login'" />

        <div v-else class="flex min-h-screen">
          <aside :class="['bg-surface border-r border-border transition-all duration-200 flex flex-col', sidebarOpen ? 'w-60' : 'w-0 overflow-hidden']"
                 aria-label="主导航">
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
            <header class="bg-surface border-b border-border h-16 flex items-center justify-between px-6 sticky top-0 z-30">
              <div class="flex items-center gap-3">
                <button @click="toggleSidebar" class="btn btn-ghost p-2" :aria-label="sidebarOpen ? '收起侧边栏' : '展开侧边栏'" :aria-expanded="sidebarOpen">
                  <span aria-hidden="true">☰</span>
                </button>
                <h1 class="text-base font-semibold text-ink">{{ currentRoute.label }}</h1>
              </div>
              <div class="flex items-center gap-2 text-sm text-ink-muted">
                <span class="badge badge-success"><span class="w-1.5 h-1.5 rounded-full bg-current" aria-hidden="true"></span>系统正常</span>
              </div>
            </header>

            <main class="flex-1 p-6 bg-surface-muted overflow-y-auto" role="main">
              <component :is="currentRoute.component" />
            </main>
          </div>
        </div>
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

  // 注册页面组件（pages/*.js 已先于本文件执行，挂载到 window.Pages）
  const P = window.Pages || {}
  const pageMap = {
    'LoginPage': P.Login, 'DashboardPage': P.Dashboard, 'AgentPage': P.Agent,
    'SkillsPage': P.Skills, 'WorkflowsPage': P.Workflows, 'KnowledgePage': P.Knowledge,
    'AuditPage': P.Audit, 'UsersPage': P.Users, 'ChannelsPage': P.Channels,
    'ConfigsPage': P.Configs
  }
  for (const [name, def] of Object.entries(pageMap)) {
    app.component(name, def || { template: '<div class="p-6 text-ink-muted">页面加载失败：' + name + '</div>' })
  }

  app.mount('#app')
  console.log('MetaPivot 管理后台已启动')
})()
