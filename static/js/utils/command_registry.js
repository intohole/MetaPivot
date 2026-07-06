/* ============================================================
   command_registry.js — 命令注册器
   挂载：window.Commands = { register, getAll, markUsed }
   供 Command Palette 消费；业务页面可自注册动作命令
   ============================================================ */
(function () {
  const STORAGE_KEY = 'metapivot_cmd_recent'
  const MAX_RECENT = 5
  const items = []
  let recent = []
  try { recent = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]') } catch (e) { recent = [] }

  /**
   * 注册命令
   * @param {Object} cmd - { id, label, icon, path?, action?, keywords?, group, shortcut? }
   *   group: 'navigation' | 'actions' | 'recent'
   */
  function register(cmd) {
    if (!cmd || !cmd.id || items.some(i => i.id === cmd.id)) return
    items.push(cmd)
  }

  /** 获取所有命令，按 group 分组（recent + navigation + actions） */
  function getAll() {
    const recentItems = recent
      .map(id => items.find(i => i.id === id))
      .filter(Boolean)
      .map(i => ({ ...i, group: 'recent' }))
    return {
      recent: recentItems,
      navigation: items.filter(i => i.group === 'navigation'),
      actions: items.filter(i => i.group === 'actions')
    }
  }

  /** 标记命令为已使用（推入 recent，持久化） */
  function markUsed(id) {
    recent = [id, ...recent.filter(r => r !== id)].slice(0, MAX_RECENT)
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(recent)) } catch (e) {}
  }

  window.Commands = { register, getAll, markUsed }
})()
