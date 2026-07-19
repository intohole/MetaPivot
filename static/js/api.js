/* ============================================================
   API 客户端 — fetch 封装
   - 统一 baseURL
   - 自动注入 JWT
   - 统一响应解析 {success, data, error}
   - 401 自动刷新令牌 + 重试一次；刷新失败才登出
   - 全局错误 Toast
   ============================================================ */
(function () {
  const BASE_URL = '/api/v1'

  function getToken() {
    return localStorage.getItem('metapivot_token') || ''
  }

  /* --- Token 刷新（单例 Promise 防并发）---
   * 多个请求同时 401 时，只发起一次 /auth/refresh，其余等待同一 Promise
   * 刷新成功 → 更新 localStorage → 返回新 token
   * 刷新失败 → 返回 null（调用方负责 logout）
   */
  let _refreshPromise = null
  async function _tryRefresh() {
    if (_refreshPromise) return _refreshPromise
    _refreshPromise = (async () => {
      try {
        const resp = await fetch(BASE_URL + '/auth/refresh', {
          method: 'POST',
          headers: { 'Authorization': 'Bearer ' + getToken(), 'Content-Type': 'application/json' }
        })
        if (!resp.ok) return null
        const body = await resp.json()
        if (!body.success || !body.data?.token) return null
        localStorage.setItem('metapivot_token', body.data.token)
        return body.data.token
      } catch (e) {
        return null
      } finally {
        _refreshPromise = null
      }
    })()
    return _refreshPromise
  }

  /** 解析 JSON 响应并处理业务错误（非 401） */
  async function _parseJson(resp, state) {
    const body = await resp.json()
    if (resp.status === 429) {
      state.notify('请求过于频繁，请稍后再试', 'warning')
      throw new Error('RATE_LIMITED')
    }
    if (!body.success) {
      const msg = body.error?.message || '请求失败'
      state.notify(msg, 'error')
      throw new Error(body.error?.code || 'REQUEST_FAILED')
    }
    return body.data
  }

  /**
   * 统一请求方法
   * @param {string} path - 路径（不含 /api/v1 前缀）
   * @param {object} options - fetch options
   * @returns {Promise<any>} - 成功返回 data，失败抛出 Error
   */
  async function request(path, options = {}) {
    const state = window.AppState
    const url = path.startsWith('http') ? path : BASE_URL + path
    const token = getToken()

    const headers = {
      'Content-Type': 'application/json',
      ...(options.headers || {})
    }
    if (token) headers['Authorization'] = 'Bearer ' + token

    state.startLoading()
    try {
      const resp = await fetch(url, { ...options, headers })
      // 非 JSON 响应（如 SSE、文件流）直接返回
      const ct = resp.headers.get('content-type') || ''
      if (!ct.includes('application/json')) {
        return resp
      }
      // 401：尝试刷新令牌并重试一次（刷新接口本身的 401 直接登出，避免死循环）
      if (resp.status === 401 && !path.startsWith('/auth/refresh')) {
        const newToken = await _tryRefresh()
        if (newToken) {
          const retryHeaders = { ...headers, 'Authorization': 'Bearer ' + newToken }
          const retryResp = await fetch(url, { ...options, headers: retryHeaders })
          const retryCt = retryResp.headers.get('content-type') || ''
          if (!retryCt.includes('application/json')) return retryResp
          if (retryResp.status === 401) {
            state.notify('登录已过期，请重新登录', 'warning')
            state.logout()
            throw new Error('UNAUTHORIZED')
          }
          return _parseJson(retryResp, state)
        }
        state.notify('登录已过期，请重新登录', 'warning')
        state.logout()
        throw new Error('UNAUTHORIZED')
      }
      if (resp.status === 401) {
        state.notify('登录已过期，请重新登录', 'warning')
        state.logout()
        throw new Error('UNAUTHORIZED')
      }
      return _parseJson(resp, state)
    } catch (e) {
      // 网络错误：TypeError 是 fetch 网络层的标准异常（跨浏览器兼容）
      // 不同浏览器 message 不一致（Chrome: "Failed to fetch", Firefox: "NetworkError when attempting to fetch", Safari: "Load failed"）
      if (e instanceof TypeError || (e.name === 'TypeError')) {
        state.notify('网络连接失败，请检查服务是否启动', 'error')
      }
      throw e
    } finally {
      state.stopLoading()
    }
  }

  // 便捷方法
  const api = {
    get: (path, params) => request(path + buildQuery(params), { method: 'GET' }),
    post: (path, body) => request(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
    put: (path, body) => request(path, { method: 'PUT', body: body ? JSON.stringify(body) : undefined }),
    del: (path) => request(path, { method: 'DELETE' }),
    upload: (path, formData) => request(path, {
      method: 'POST',
      body: formData,
      headers: {}  // 让浏览器自动设置 multipart boundary
    }),
    // SSE 流式订阅（用于 Agent 任务流）— 支持断线自动重连
    // opts: { maxRetries=5, onReconnect=(attempt, delay, error)=>void, retryDelayMs=1000 }
    // 返回 cancel 函数：调用后不再重连
    streamSSE: (path, onEvent, onError, onClose, opts = {}) => {
      const token = getToken()
      const url = BASE_URL + path
      const maxRetries = opts.maxRetries !== undefined ? opts.maxRetries : 5
      const baseDelay = opts.retryDelayMs || 1000
      let ctrl = null
      let manualAbort = false
      let retryCount = 0
      let reconnectTimer = null

      async function connect() {
        ctrl = new AbortController()
        try {
          const resp = await fetch(url, {
            headers: { 'Authorization': 'Bearer ' + token, 'Accept': 'text/event-stream' },
            signal: ctrl.signal
          })
          // 非 200 直接报错（401/403/404 等不重试）
          if (!resp.ok) {
            const err = new Error('SSE_HTTP_' + resp.status)
            err.status = resp.status
            throw err
          }
          // 重连成功：重置计数
          if (retryCount > 0) {
            retryCount = 0
            opts.onReconnect && opts.onReconnect(0, 0, null)
          }
          const reader = resp.body.getReader()
          const dec = new TextDecoder()
          let buf = ''
          while (true) {
            const { value, done } = await reader.read()
            if (done) { onClose && onClose(); break }
            buf += dec.decode(value, { stream: true })
            const lines = buf.split('\n')
            buf = lines.pop()
            let ev = {}
            for (const ln of lines) {
              if (ln.startsWith('event:')) ev.event = ln.slice(6).trim()
              else if (ln.startsWith('data:')) ev.data = ln.slice(5).trim()
              else if (ln === '' && ev.event) { onEvent(ev); ev = {} }
            }
          }
        } catch (e) {
          // 手动取消不重连
          if (manualAbort) return
          // HTTP 错误（401/403/404）不重试，直接报错
          if (e.status && e.status >= 400 && e.status < 500) {
            onError && onError(e)
            return
          }
          // 网络错误：尝试重连（指数退避：1s→2s→4s→8s→16s）
          if (retryCount < maxRetries) {
            retryCount++
            const delay = Math.min(baseDelay * Math.pow(2, retryCount - 1), 16000)
            opts.onReconnect && opts.onReconnect(retryCount, delay, e)
            reconnectTimer = setTimeout(() => connect(), delay)
          } else {
            // 超过最大重试次数，通知调用方
            onError && onError(e)
          }
        }
      }

      connect()

      // 返回取消函数：设置 manualAbort 阻止重连，并 abort 当前连接
      return () => {
        manualAbort = true
        if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
        if (ctrl) ctrl.abort()
      }
    }
  }

  function buildQuery(params) {
    if (!params) return ''
    const usp = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== '') usp.append(k, v)
    }
    const q = usp.toString()
    return q ? '?' + q : ''
  }

  window.API = api
})()
