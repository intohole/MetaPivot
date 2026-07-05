/* ============================================================
   全局状态管理 — 挂载到 window.AppState
   所有跨组件共享状态都通过此对象访问
   - 用户/路由/Toast/加载态
   - Round 4: theme 暗色模式 + confirmAction 全局确认对话框
   ============================================================ */
(function () {
  const { ref, reactive, computed } = Vue

  // 用户信息（登录态）
  const user = ref(null)
  // 加载中计数器（多个并发请求时合并）
  const loadingCount = ref(0)
  const loading = computed(() => loadingCount.value > 0)
  // 全局 Toast 列表
  const toasts = reactive([])
  let toastSeq = 0

  // 路由状态：hash 路由 #/page
  const currentRoute = ref(window.location.hash.slice(1) || '/dashboard')

  // Round 4: 主题状态（light/dark），localStorage 持久化
  const theme = ref(localStorage.getItem('metapivot_theme') || 'light')
  function toggleTheme() {
    theme.value = theme.value === 'light' ? 'dark' : 'light'
    localStorage.setItem('metapivot_theme', theme.value)
    document.documentElement.dataset.theme = theme.value
  }
  // 启动时同步到 documentElement（避免 FOUC 闪烁）
  document.documentElement.dataset.theme = theme.value

  // Round 4: 全局确认对话框状态（替代原生 confirm()）
  const confirmState = reactive({
    visible: false, title: '确认操作', message: '', confirmText: '确认', danger: false, resolve: null
  })
  function confirmAction(opts) {
    return new Promise((resolve) => {
      confirmState.visible = true
      confirmState.title = opts.title || '确认操作'
      confirmState.message = opts.message || ''
      confirmState.confirmText = opts.confirmText || '确认'
      confirmState.danger = opts.danger || false
      confirmState.resolve = resolve
    })
  }
  function resolveConfirm(action) {
    const resolve = confirmState.resolve
    confirmState.visible = false
    confirmState.resolve = null
    if (resolve) resolve(action)
  }

  // 从 localStorage 恢复登录态
  function restoreAuth() {
    try {
      const token = localStorage.getItem('metapivot_token')
      const userStr = localStorage.getItem('metapivot_user')
      if (token && userStr) {
        user.value = JSON.parse(userStr)
      }
    } catch (e) {
      console.warn('Restore auth failed:', e)
      logout()
    }
  }

  function setAuth(token, userData) {
    localStorage.setItem('metapivot_token', token)
    localStorage.setItem('metapivot_user', JSON.stringify(userData))
    user.value = userData
  }

  function logout() {
    localStorage.removeItem('metapivot_token')
    localStorage.removeItem('metapivot_user')
    user.value = null
    navigate('/login')
  }

  // Toast 通知：type = info | success | warning | error
  function notify(message, type = 'info', duration = 3000) {
    const id = ++toastSeq
    toasts.push({ id, message, type, duration })
    if (duration > 0) {
      setTimeout(() => dismissToast(id), duration)
    }
    return id
  }
  function dismissToast(id) {
    const idx = toasts.findIndex(t => t.id === id)
    if (idx >= 0) toasts.splice(idx, 1)
  }

  // 路由跳转
  function navigate(path) {
    if (currentRoute.value !== path) {
      currentRoute.value = path
      window.location.hash = path
    }
  }

  // 监听 hash 变化
  window.addEventListener('hashchange', () => {
    currentRoute.value = window.location.hash.slice(1) || '/dashboard'
  })

  // 权限判断
  function hasRole(role) {
    if (!user.value) return false
    if (role === 'admin') return user.value.role === 'admin'
    if (role === 'manager') return ['admin', 'manager'].includes(user.value.role)
    return true
  }

  // 加载计数器
  function startLoading() { loadingCount.value++ }
  function stopLoading() { loadingCount.value = Math.max(0, loadingCount.value - 1) }

  // 暴露到 window，确保跨文件可访问
  window.AppState = {
    user, loading, loadingCount, toasts, currentRoute,
    theme, toggleTheme,
    confirmState, confirmAction, resolveConfirm,
    setAuth, logout, restoreAuth,
    notify, dismissToast,
    navigate, hasRole,
    startLoading, stopLoading
  }

  // 启动时恢复登录态
  restoreAuth()
})()
