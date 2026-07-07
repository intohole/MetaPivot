/* skill_actions.js — Skill 沉淀跨页面 helper
 * 提供：录制 task → skill、workflow → skill、LLM 抽取草稿 review
 * 挂载：window.SkillActions
 * 供 agent.js（任务完成后保存）+ workflows.js（复制为 Skill）复用
 */
(function () {
  const SkillActions = {
    /**
     * 从 Agent 任务 LLM 抽取草稿 → confirmAction 确认 → 录制为 skill
     * @param {string} taskId - Agent 任务 ID
     * @param {object} state - window.AppState（提供 notify/confirmAction）
     * @returns {Promise<object|null>} 保存的 skill 或 null
     */
    async extractAndSave(taskId, state) {
      if (!taskId) { state.notify('无任务 ID', 'error'); return null }
      try {
        state.notify('LLM 正在分析任务轨迹...', 'info')
        const draft = await window.API.post('/skills/extract-from-task/' + taskId, {})
        if (!draft || !draft.name) {
          state.notify('LLM 抽取失败：未返回有效草稿', 'error')
          return null
        }
        // 展示草稿关键信息供用户决策（skill_review_modal 暂未实现，用 confirmAction 简化）
        const confidence = draft.confidence != null ? (draft.confidence * 100).toFixed(0) + '%' : 'N/A'
        const msg = [
          '名称：' + draft.name,
          '描述：' + (draft.description || '(无)'),
          '置信度：' + confidence,
          draft.reasoning ? '理由：' + draft.reasoning : '',
          '步骤数：' + (draft.step_count || 0),
        ].filter(Boolean).join('\n')
        const action = await state.confirmAction({
          title: '保存为 Skill', message: msg, confirmText: '保存',
        })
        if (action !== 'confirm') return null
        const saved = await window.API.post('/skills/from-task/' + taskId, {
          name: draft.name,
          description: draft.description || '从任务录制',
          tags: draft.suggested_tags || [],
        })
        state.notify('Skill 保存成功：' + (saved.name || draft.name), 'success')
        return saved
      } catch (e) {
        state.notify('保存失败：' + (e.message || '未知错误'), 'error')
        return null
      }
    },

    /**
     * 从现有 workflow 创建 skill（一键沉淀）
     * @param {string} workflowId - Workflow ID
     * @param {object} state - window.AppState
     * @returns {Promise<object|null>} 创建的 skill 或 null
     */
    async fromWorkflow(workflowId, state) {
      if (!workflowId) { state.notify('无工作流 ID', 'error'); return null }
      const name = window.prompt('请输入 Skill 名称：')
      if (!name || !name.trim()) return null
      try {
        const saved = await window.API.post('/skills/from-workflow/' + workflowId, {
          name: name.trim(),
          description: '从工作流创建',
          tags: [],
        })
        state.notify('Skill 创建成功：' + (saved.name || name.trim()), 'success')
        return saved
      } catch (e) {
        state.notify('创建失败：' + (e.message || '未知错误'), 'error')
        return null
      }
    },
  }
  window.SkillActions = SkillActions
})()
