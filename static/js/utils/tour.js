/* ============================================================
   新手引导引擎 — driver.js 1.3.1 封装
   - 导出 window.startTour()，dashboard.js 调用
   - tour 限定 dashboard 单页（driver.js 1.x 不原生支持 SPA 路由切换）
   - 复用 app.js / dashboard.js 的 data-tour 锚点：sidebar / header / quick-actions
   - 首次走完 localStorage 标记 metapivot_tour_done=1，不重复自动弹（手动可再触发）
   - CDN 全局：window.driver.js.driver（嵌套结构，注意不是 window.driver）
   ============================================================ */
(function () {
  window.startTour = function () {
    // 防御：driver.js CDN 加载失败时静默退出
    if (!window.driver || !window.driver.js || !window.driver.js.driver) {
      console.warn('[tour] driver.js not loaded, skip tour')
      return
    }
    const driver = window.driver.js.driver

    const driverObj = driver({
      showProgress: true,
      allowClose: true,
      nextBtnText: '下一步 →',
      prevBtnText: '← 上一步',
      doneBtnText: '完成 ✓',
      steps: [
        {
          popover: {
            title: '欢迎使用 MetaPivot',
            description: '企业 IM 自动化办公服务，提供超级 Agent、可视化工作流、Skill 能力体系。3 步快速上手，点击「下一步」开始引导。'
          }
        },
        {
          element: '[data-tour="sidebar"]',
          popover: {
            title: '主导航',
            description: '通过左侧导航在各功能模块间切换：仪表盘、Agent 对话、Skill 管理、工作流、知识库等。',
            side: 'right',
            align: 'start'
          }
        },
        {
          element: '[data-tour="header"]',
          popover: {
            title: '顶栏',
            description: '点击 🌙/☀️ 切换暗色/亮色模式（自动持久化）；右侧徽章显示系统运行状态。',
            side: 'bottom',
            align: 'start'
          }
        },
        {
          element: '[data-tour="quick-actions"]',
          popover: {
            title: '快捷操作',
            description: '从这里快速发起 Agent 对话、创建 Skill、配置工作流或上传知识文档。新手也可点击右侧「🎓 新手引导」重新打开本引导。',
            side: 'top',
            align: 'center'
          }
        },
        {
          popover: {
            title: '开始体验',
            description: '引导结束。建议下一步：① 在「系统配置」填入 LLM_API_KEY；② 到「Agent 对话」发起一次测试对话；③ 接入 IM 渠道（钉钉/企微/飞书）。'
          }
        }
      ]
    })

    driverObj.drive()
    // 标记已完成，避免下次自动触发（手动触发不在此函数控制，由调用方决定）
    try { localStorage.setItem('metapivot_tour_done', '1') } catch (e) {}
  }
})()
