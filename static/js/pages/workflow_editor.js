/* ============================================================
   WorkflowEditor — Drawflow DAG 可视化编辑器
   - 11 节点类型分色（与后端 engine.py NODE_TYPES 对齐）
   - 左侧节点面板（点击添加）
   - 中间画布（Drawflow，连线/删除）
   - 画布↔JSON 双向同步（loadJSON / exportJSON）
   - 挂载到 window.WorkflowEditor
   ============================================================ */
(function () {
  // 节点类型定义（type, label, icon, color, inputs, outputs）
  const NODE_TYPES = {
    start:        { label: '开始',       icon: '▶️', color: '#dcfce7', inputs: 0, outputs: 1 },
    end:          { label: '结束',       icon: '⏹️', color: '#fee2e2', inputs: 1, outputs: 0 },
    skill_call:   { label: 'Skill 调用',  icon: '🧩', color: '#f3e8ff', inputs: 1, outputs: 1 },
    llm_call:     { label: 'LLM 调用',   icon: '🤖', color: '#dbeafe', inputs: 1, outputs: 1 },
    condition:    { label: '条件分支',   icon: '🔀', color: '#fef3c7', inputs: 1, outputs: 2 },
    send_message: { label: '发送消息',   icon: '📨', color: '#cffafe', inputs: 1, outputs: 1 },
    hitl:         { label: '人工确认',   icon: '✋', color: '#fef9c3', inputs: 1, outputs: 1 },
    parallel:     { label: '并行执行',   icon: '⚡', color: '#fce7f3', inputs: 1, outputs: 1 },
    agent_call:   { label: 'Agent 调用', icon: '🎯', color: '#e0e7ff', inputs: 1, outputs: 1 },
    sub_workflow: { label: '子工作流',   icon: '📦', color: '#f3f4f6', inputs: 1, outputs: 1 },
    http_request: { label: 'HTTP 请求',  icon: '🌐', color: '#d1fae5', inputs: 1, outputs: 1 }
  }

  let editor = null
  let container = null
  let onJsonChange = null

  function init(containerEl, onChange) {
    container = containerEl
    onJsonChange = onChange
    editor = new Drawflow(container)
    editor.reroute = true
    editor.force_first_input = true
    editor.start()
    editor.on('connectionCreated', emitChange)
    editor.on('connectionRemoved', emitChange)
    editor.on('nodeCreated', emitChange)
    editor.on('nodeRemoved', emitChange)
    return editor
  }

  function emitChange() {
    if (onJsonChange) { try { onJsonChange(exportJSON()) } catch (e) {} }
  }

  function addNode(type, posX, posY) {
    const cfg = NODE_TYPES[type]
    if (!cfg || !editor) return null
    const html = '<div class="wf-node-content"><span class="wf-node-icon">' + cfg.icon +
      '</span><span class="wf-node-label">' + cfg.label + '</span></div>'
    const nodeId = editor.addNode(
      type, cfg.inputs, cfg.outputs, posX || 100, posY || 100,
      'node-' + type, html, {}
    )
    emitChange()
    return nodeId
  }

  function loadJSON(definition) {
    if (!editor) return
    editor.clear()
    const nodes = (definition && definition.nodes) || []
    const edges = (definition && definition.edges) || []
    const idMap = {}  // 原 node.id → Drawflow 数字 id
    nodes.forEach(function (n, i) {
      const cfg = NODE_TYPES[n.type] || { icon: '❓', label: n.type, inputs: 1, outputs: 1 }
      const html = '<div class="wf-node-content"><span class="wf-node-icon">' + cfg.icon +
        '</span><span class="wf-node-label">' + cfg.label + '</span></div>'
      const posX = (n.position && n.position.x) || (100 + i * 200)
      const posY = (n.position && n.position.y) || (100 + (i % 3) * 120)
      const nodeId = editor.addNode(n.type, cfg.inputs, cfg.outputs, posX, posY, 'node-' + n.type, html, { config: n.config || {} })
      idMap[n.id] = nodeId
    })
    edges.forEach(function (e) {
      const from = idMap[e.from]
      const to = idMap[e.to]
      if (from && to) editor.addConnection(from, to, 'output_1', 'input_1')
    })
  }

  function exportJSON() {
    if (!editor) return { nodes: [], edges: [], variables: [] }
    const data = editor.export().drawflow.Module.data
    const nodes = []
    const edges = []
    Object.keys(data).forEach(function (id) {
      const n = data[id]
      nodes.push({
        id: 'n' + id,
        type: (n.class || '').replace('node-', ''),
        config: (n.data && n.data.config) || {},
        position: { x: n.pos_x, y: n.pos_y }
      })
      if (n.outputs) {
        Object.keys(n.outputs).forEach(function (outKey) {
          const out = n.outputs[outKey]
          if (out.connections) {
            out.connections.forEach(function (conn) {
              edges.push({ from: 'n' + id, to: 'n' + conn.node, output: outKey, input: conn.input })
            })
          }
        })
      }
    })
    return { nodes: nodes, edges: edges, variables: [] }
  }

  function renderPalette(panelEl) {
    panelEl.innerHTML = Object.keys(NODE_TYPES).map(function (type) {
      const cfg = NODE_TYPES[type]
      return '<div class="wf-palette-item" data-type="' + type + '" draggable="true" title="点击添加 ' + cfg.label + '">' +
        '<span class="wf-node-icon">' + cfg.icon + '</span>' +
        '<span class="wf-node-label">' + cfg.label + '</span></div>'
    }).join('')
    panelEl.querySelectorAll('.wf-palette-item').forEach(function (item) {
      item.addEventListener('click', function () {
        const type = item.dataset.type
        addNode(type, 200 + Math.random() * 100, 100 + Math.random() * 100)
      })
    })
  }

  function destroy() {
    if (editor) { editor.clear(); editor = null }
    if (container) container.innerHTML = ''
    container = null
    onJsonChange = null
  }

  window.WorkflowEditor = {
    init: init,
    loadJSON: loadJSON,
    exportJSON: exportJSON,
    addNode: addNode,
    renderPalette: renderPalette,
    destroy: destroy,
    NODE_TYPES: NODE_TYPES
  }
})()
