/* ============================================================
   API 客户端 — fetch 封装
   - 统一 baseURL
   - 自动注入 JWT
   - 统一响应解析 {success, data, error}
   - 401 自动登出
   - 全局错误 Toast
   ============================================================ */
(function () {
  const BASE_URL = '/api/v1'

  function getToken() {
    return localStorage.getItem('metapivot_token') || ''
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
      const body = await resp.json()
      // 401 未授权：清理登录态并跳转
      if (resp.status === 401) {
        state.notify('登录已过期，请重新登录', 'warning')
        state.logout()
        throw new Error('UNAUTHORIZED')
      }
      // 429 限流
      if (resp.status === 429) {
        state.notify('请求过于频繁，请稍后再试', 'warning')
        throw new Error('RATE_LIMITED')
      }
      // 业务错误
      if (!body.success) {
        const msg = body.error?.message || '请求失败'
        state.notify(msg, 'error')
        throw new Error(body.error?.code || 'REQUEST_FAILED')
      }
      return body.data
    } catch (e) {
      // 网络错误
      if (e.message === 'Failed to fetch') {
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
    // SSE 流式订阅（用于 Agent 任务流）
    streamSSE: (path, onEvent, onError, onClose) => {
      const token = getToken()
      const url = BASE_URL + path
      const ctrl = new AbortController()
      fetch(url, {
        headers: { 'Authorization': 'Bearer ' + token, 'Accept': 'text/event-stream' },
        signal: ctrl.signal
      }).then(async resp => {
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
      }).catch(e => { onError && onError(e) })
      return () => ctrl.abort()
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
