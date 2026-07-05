/* ============================================================
   Markdown 渲染工具 — marked + highlight.js + DOMPurify
   - GFM + 换行符支持
   - 代码高亮（hljs 后处理，兼容 marked 12.x）
   - XSS 防护（DOMPurify sanitize）
   - 挂载到 window.renderMarkdown
   ============================================================ */
(function () {
  // marked 基础配置（GFM + 换行）
  if (window.marked) {
    marked.setOptions({ breaks: true, gfm: true })
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    })
  }

  /**
   * 渲染 Markdown 为安全 HTML
   * @param {string} text - Markdown 文本
   * @returns {string} sanitize 后的 HTML
   */
  window.renderMarkdown = function (text) {
    if (!text) return ''
    try {
      // 1. marked 解析为 HTML
      var raw = window.marked ? marked.parse(text) : escapeHtml(text)
      // 2. DOMPurify 清洗 XSS
      var clean = window.DOMPurify ? DOMPurify.sanitize(raw) : raw
      // 3. 代码高亮后处理（兼容 marked 12.x，用 DOMParser 解析后对 pre>code 应用 hljs）
      if (window.hljs && clean.indexOf('<pre>') >= 0) {
        var doc = new DOMParser().parseFromString(clean, 'text/html')
        var blocks = doc.querySelectorAll('pre code')
        blocks.forEach(function (block) {
          try {
            var lang = (block.className.match(/language-(\w+)/) || [])[1]
            var result = lang && hljs.getLanguage(lang)
              ? hljs.highlight(block.textContent, { language: lang })
              : hljs.highlightAuto(block.textContent)
            block.innerHTML = result.value
            block.classList.add('hljs')
          } catch (e) { /* 高亮失败保留原文 */ }
        })
        return doc.body.innerHTML
      }
      return clean
    } catch (e) {
      return escapeHtml(text)
    }
  }
})()
