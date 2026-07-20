/* 通用格式化工具 — 统一各页面的时间/文本格式化（消除重复定义） */
(function () {
  window.Format = {
    /** ISO 时间 → 'YYYY/M/D HH:mm:ss'（zh-CN 24小时制），空值显示 '-' */
    time(t) {
      return t ? new Date(t).toLocaleString('zh-CN', { hour12: false }) : '-'
    },
  }
})()
