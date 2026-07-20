/* 列表页组合式函数 — 统一分页加载/搜索/翻页模式（消除各页面重复的 loadList/onPageChange/onSearch）
 *
 * 用法：
 *   const lp = window.useListPage('/skills', {                // path 也支持函数：() => tab.value==='a' ? '/a' : '/b'
 *     extraParams: () => ({ enabled: true }),   // 可选：附加查询参数（响应式，每次请求时求值）
 *     failMsg: '加载 Skill 列表失败',            // 可选：错误提示前缀
 *     pageSize: 20,                              // 可选：默认每页数量
 *     withKeyword: true,                         // 可选：是否带 keyword 参数（默认 true）
 *     onPageChange: () => { selectedKeys.value = [] },  // 可选：翻页附加动作（如清空选择）
 *   })
 *   setup() 返回: ...lp  // list, total, page, pageSize, keyword, loading, loadList, onPageChange, onSearch
 */
(function () {
  window.useListPage = function (path, options) {
    const { ref } = Vue
    const opts = options || {}
    const list = ref([])
    const total = ref(0)
    const page = ref(1)
    const pageSize = ref(opts.pageSize || 20)
    const keyword = ref('')
    const loading = ref(false)

    const loadList = async () => {
      loading.value = true
      try {
        const params = { page: page.value, page_size: pageSize.value }
        if (opts.withKeyword !== false) params.keyword = keyword.value
        if (opts.extraParams) Object.assign(params, opts.extraParams())
        const p = typeof path === 'function' ? path() : path  // 支持动态路径（如 tab 切换）
        const res = await window.API.get(p, params)
        list.value = res.items || []
        total.value = res.total || 0
      } catch (e) {
        window.AppState.notify((opts.failMsg || '加载列表失败') + '：' + (e.message || '未知错误'), 'error')
      } finally { loading.value = false }
    }

    const onPageChange = ({ page: p, pageSize: ps }) => {
      page.value = p
      if (ps) pageSize.value = ps
      if (opts.onPageChange) opts.onPageChange()
      loadList()
    }
    const onSearch = () => { page.value = 1; loadList() }

    return { list, total, page, pageSize, keyword, loading, loadList, onPageChange, onSearch }
  }
})()
