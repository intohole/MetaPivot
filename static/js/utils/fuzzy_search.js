/* ============================================================
   fuzzy_search.js — 轻量模糊匹配（子序列 + 评分）
   挂载：window.fuzzySearch(items, query, opts) → [{item, score}]
   无 CDN 依赖，~70 行覆盖 90% 场景（候选 ≤ 50 项无性能压力）
   ============================================================ */
(function () {
  /**
   * 子序列匹配 + 评分
   * @param {Array} items - 原始数组
   * @param {String} query - 查询串（小写）
   * @param {Object} opts - { keys: ['label','keywords'] }
   * @returns {Array} [{item, score}] 按 score 降序
   */
  function fuzzySearch(items, query, opts) {
    opts = opts || {}
    const keys = opts.keys || ['label']
    const q = query.toLowerCase()
    if (!q) return items.map(item => ({ item, score: 0 }))

    const results = []
    for (const item of items) {
      let bestScore = -1
      for (const key of keys) {
        const text = String(item[key] || '').toLowerCase()
        const score = scoreMatch(text, q)
        if (score > bestScore) bestScore = score
      }
      if (bestScore >= 0) results.push({ item, score: bestScore })
    }
    return results.sort((a, b) => b.score - a.score)
  }

  /**
   * 评分算法：
   * - 首字符匹配 +2（"da" 匹配 "dashboard" 首字母）
   * - 连续匹配 +1（"das" 连续匹配 "dashboard" 前三字符）
   * - 词边界匹配 +3（"home" 匹配 "dashboard home" 的 home 词）
   * - 不匹配返回 -1
   */
  function scoreMatch(text, query) {
    if (!text) return -1
    if (text.includes(query)) {
      // 完整包含，额外加分
      let score = query.length * 2
      if (text.startsWith(query)) score += 5
      const idx = text.indexOf(query)
      if (idx > 0 && /[\s\-_]/.test(text[idx - 1])) score += 3
      return score
    }
    // 子序列匹配
    let qi = 0, score = 0, prevMatched = false
    for (let ti = 0; ti < text.length && qi < query.length; ti++) {
      if (text[ti] === query[qi]) {
        score += prevMatched ? 1 : 2
        if (ti === 0) score += 2
        if (ti > 0 && /[\s\-_]/.test(text[ti - 1])) score += 3
        prevMatched = true
        qi++
      } else {
        prevMatched = false
      }
    }
    return qi === query.length ? score : -1
  }

  window.fuzzySearch = fuzzySearch
})()
