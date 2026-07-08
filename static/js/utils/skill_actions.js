/* skill_actions.js — Skill 沉淀数据层（纯 API 调用，不处理 UI）
 *
 * 职责分离：本模块只负责 API 调用 + 数据返回，UI（Modal/prompt）由调用方页面处理。
 * 提供：
 *   - extractDraft(taskId): LLM 抽取任务轨迹 → 返回草稿数据
 *   - saveFromTask(taskId, form): 从任务录制 Skill
 *   - saveFromWorkflow(workflowId, form): 从工作流创建 Skill
 *
 * 挂载：window.SkillActions
 * 供 agent.js（任务完成后保存）+ workflows.js（复制为 Skill）复用
 */
(function () {
  const SkillActions = {
    /**
     * LLM 抽取任务轨迹 → 返回草稿数据（不弹 UI）
     * @param {string} taskId - Agent 任务 ID
     * @returns {Promise<object>} draft = { name, description, confidence, reasoning, step_count, suggested_tags }
     */
    async extractDraft(taskId) {
      if (!taskId) throw new Error('无任务 ID')
      const draft = await window.API.post('/skills/extract-from-task/' + taskId, {})
      if (!draft || !draft.name) throw new Error('LLM 抽取失败：未返回有效草稿')
      return draft
    },

    /**
     * 从 Agent 任务录制 Skill（不弹 UI）
     * @param {string} taskId - Agent 任务 ID
     * @param {object} form - { name, description, tags }
     * @returns {Promise<object>} 创建的 Skill
     */
    async saveFromTask(taskId, form) {
      if (!taskId) throw new Error('无任务 ID')
      return await window.API.post('/skills/from-task/' + taskId, {
        name: form.name || '',
        description: form.description || '从任务录制',
        tags: form.tags || [],
      })
    },

    /**
     * 从现有 workflow 创建 skill（不弹 UI）
     * @param {string} workflowId - Workflow ID
     * @param {object} form - { name, description, tags }
     * @returns {Promise<object>} 创建的 Skill
     */
    async saveFromWorkflow(workflowId, form) {
      if (!workflowId) throw new Error('无工作流 ID')
      return await window.API.post('/skills/from-workflow/' + workflowId, {
        name: form.name || '',
        description: form.description || '从工作流创建',
        tags: form.tags || [],
      })
    },

    /**
     * 兼容旧调用：extractAndSave(taskId, state) — 逐步迁移到 extractDraft + 页面 Modal
     * 保留用于尚未迁移的调用方，新代码请用 extractDraft + saveFromTask
     */
    async extractAndSave(taskId, state) {
      if (!taskId) { state.notify('无任务 ID', 'error'); return null }
      try {
        state.notify('LLM 正在分析任务轨迹...', 'info')
        const draft = await this.extractDraft(taskId)
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
        const saved = await this.saveFromTask(taskId, {
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
  }
  window.SkillActions = SkillActions
})()
