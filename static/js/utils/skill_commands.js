/* ============================================================
   skill_commands.js — Skill 快捷执行命令注册
   挂载：window.SkillCommands.init()
   供 Command Palette ⌘K 消费：用户输入 /skill-name 或搜索 skill 名
   选中后进入参数输入模式，提交后调用 POST /api/v1/skills/{id}/execute
   Sprint 8.2
   ============================================================ */
(function () {
  const AppState = window.AppState

  /** 根据 input_schema 生成参数输入提示 */
  function buildPrompt(skill) {
    const schema = skill.input_schema || {}
    const props = schema.properties || {}
    const required = schema.required || []
    const fields = Object.keys(props)
    if (fields.length === 0) return '此 Skill 无需参数，直接回车执行'
    // 简单 schema 提示字段名，复杂 schema 提示 JSON
    if (fields.length <= 3) {
      const hints = fields.map(f => {
        const desc = props[f].description || props[f].type || 'string'
        const req = required.includes(f) ? '必填' : '可选'
        return `${f}(${req}:${desc})`
      })
      return `输入参数 ${hints.join(', ')}，用 key=value 分隔（多值用 & 连接）`
    }
    return `输入 JSON 参数（字段: ${fields.join(', ')}）`
  }

  /** 解析用户输入为 args dict
   * 支持两种格式：
   *   1. key1=value1&key2=value2（简单 kv）
   *   2. {"key":"value"}（JSON）
   */
  function parseArgs(input, skill) {
    const schema = skill.input_schema || {}
    const props = schema.properties || {}
    const trimmed = input.trim()
    if (!trimmed) return {}

    // JSON 格式
    if (trimmed.startsWith('{')) {
      try { return JSON.parse(trimmed) } catch (e) {
        throw new Error('JSON 格式错误：' + e.message)
      }
    }

    // key=value&key=value 格式
    const args = {}
    const pairs = trimmed.split('&').map(p => p.trim()).filter(Boolean)
    for (const pair of pairs) {
      const eqIdx = pair.indexOf('=')
      if (eqIdx === -1) {
        // 无 = 号，若 schema 只有一个字段则赋给该字段
        const fields = Object.keys(props)
        if (fields.length === 1) {
          args[fields[0]] = pair
          continue
        }
        throw new Error(`参数格式错误：${pair}（应为 key=value）`)
      }
      const k = pair.slice(0, eqIdx).trim()
      let v = pair.slice(eqIdx + 1).trim()
      // 类型转换
      const type = props[k] && props[k].type
      if (type === 'integer' || type === 'number') {
        const num = Number(v)
        if (isNaN(num)) throw new Error(`参数 ${k} 应为数字`)
        v = num
      } else if (type === 'boolean') {
        v = (v === 'true' || v === '1')
      }
      args[k] = v
    }
    return args
  }

  /** 执行 Skill 调用 — 复用 window.API（自动注入 JWT + 统一错误处理） */
  async function executeSkill(skill, args) {
    // window.API.post 自动注入 Bearer token、解析 {success, data}、401 自动登出
    return await window.API.post(`/skills/${skill.id}/execute`, { input: args })
  }

  /** 结果展示：通过全局 notify + 跳转 agent 页显示详情 */
  function showResult(skill, result) {
    const str = typeof result === 'string' ? result : JSON.stringify(result, null, 2)
    const preview = str.length > 300 ? str.slice(0, 300) + '…' : str
    if (AppState.notify) {
      AppState.notify(`✅ Skill "${skill.name}" 执行完成`, 'success')
    }
    // 将结果作为 Agent 消息展示（复用 Agent 页面的对话流）
    AppState.pendingMessage = `/skill ${skill.name} 执行结果：\n${preview}`
    AppState.navigate('/agent')
  }

  /** 加载 enabled skills 并注册为命令 */
  async function init() {
    if (!window.Commands || !window.API) return
    // 未登录则跳过（window.API 自动注入 token，但未登录时返回 401 触发登出，故先检查）
    if (!window.AppState.user || !window.AppState.user.value) return
    try {
      // window.API.get 自动注入 Bearer token + 解析 {success, data}
      const data = await window.API.get('/skills', { enabled: true, page_size: 50 })
      const skills = (data && data.items) || data || []
      skills.forEach(skill => {
        const cmdId = `skill-exec-${skill.id}`
        // 已注册则跳过（避免重复）
        if (window.Commands.getAll().actions.some(c => c.id === cmdId)) return
        window.Commands.register({
          id: cmdId,
          label: `执行 Skill：${skill.name}`,
          icon: '⚡',
          keywords: `skill execute ${skill.name} ${skill.tags ? skill.tags.join(' ') : ''}`,
          group: 'actions',
          inputPrompt: buildPrompt(skill),
          action: async (input) => {
            try {
              const args = parseArgs(input, skill)
              const result = await executeSkill(skill, args)
              showResult(skill, result)
            } catch (e) {
              if (AppState.notify) {
                AppState.notify(`❌ Skill 执行失败：${e.message}`, 'error')
              }
            }
          },
        })
      })
    } catch (e) {
      console.warn('[SkillCommands] load failed:', e)
    }
  }

  window.SkillCommands = { init, parseArgs, buildPrompt }
})()
