/* ============================================================
   command_palette.js — ⌘K 命令面板组件
   挂载：window.Components.CommandPalette
   自实现浮层（非 <dialog>），复用 focus_trap.js
   ARIA: combobox + listbox + option + aria-activedescendant
   ============================================================ */
(function () {
  const { ref, computed, nextTick, onMounted, onUnmounted } = Vue
  const Components = window.Components || (window.Components = {})

  Components.CommandPalette = {
    name: 'CommandPalette',
    setup() {
      const state = window.AppState
      const visible = ref(false)
      const query = ref('')
      const activeId = ref('')
      const rootRef = ref(null)
      const inputRef = ref(null)
      const listRef = ref(null)
      let releaseTrap = null
      // 参数化命令输入模式（inputPrompt 命令收集用户输入后再执行）
      const inputMode = ref(false)
      const inputValue = ref('')
      const pendingCmd = ref(null)
      // v-model 不能用三元表达式（Vue compiler-42），用 writable computed 桥接两种输入态
      const currentInput = computed({
        get: () => inputMode.value ? inputValue.value : query.value,
        set: (v) => { if (inputMode.value) inputValue.value = v; else query.value = v }
      })

      // 分组计算：无 query 时显示 recent + navigation + actions；有 query 时 fuzzy 过滤
      // NL 中枢：fuzzy 无匹配时，提供自然语言 fallback（发送给 Agent / 搜索知识库）
      const groups = computed(() => {
        const all = window.Commands ? window.Commands.getAll() : { recent: [], navigation: [], actions: [] }
        const q = query.value.trim().toLowerCase()
        if (!q) {
          return [
            { label: '最近使用', items: all.recent },
            { label: '导航', items: all.navigation },
            { label: '动作', items: all.actions }
          ].filter(g => g.items.length > 0)
        }
        const flat = [...all.recent, ...all.navigation, ...all.actions]
        const matched = window.fuzzySearch(flat, q, { keys: ['label', 'keywords', 'id'] })
          .map(m => m.item)
        if (matched.length > 0) {
          return [{ label: '搜索结果', items: matched }]
        }
        // NL fallback：无匹配时生成自然语言动作建议
        const origQ = query.value.trim()
        return [{ label: '自然语言命令', items: [
          { id: '_nl-agent', label: '发送给 Agent：' + origQ, icon: '🤖',
            action: () => { state.pendingMessage.value = origQ; state.navigate('/agent') } },
          { id: '_nl-knowledge', label: '搜索知识库：' + origQ, icon: '📚',
            action: () => { state.pendingQuery.value = origQ; state.navigate('/knowledge') } },
          { id: '_nl-skill', label: '查找 Skill：' + origQ, icon: '🧩',
            action: () => { state.pendingAction.value = 'search-skill:' + origQ; state.navigate('/skills') } }
        ]}]
      })

      // 扁平化所有可见项（用于键盘导航）
      const flatItems = computed(() => groups.value.flatMap(g => g.items))

      // 打开时初始化
      const open = () => {
        visible.value = true
        query.value = ''
        nextTick(() => {
          if (inputRef.value) inputRef.value.focus()
          if (rootRef.value && window.trapFocus) {
            releaseTrap = window.trapFocus(rootRef.value, { initialFocus: inputRef.value })
          }
          // 默认高亮第一项
          if (flatItems.value.length > 0) activeId.value = flatItems.value[0].id
        })
      }

      const close = () => {
        visible.value = false
        inputMode.value = false
        pendingCmd.value = null
        inputValue.value = ''
        if (releaseTrap) { releaseTrap(); releaseTrap = null }
      }

      // 执行命令
      const execute = (item) => {
        if (!item) return
        // 参数化命令：切到输入模式收集参数
        if (item.inputPrompt) {
          pendingCmd.value = item
          inputValue.value = ''
          inputMode.value = true
          nextTick(() => { if (inputRef.value) inputRef.value.focus() })
          return
        }
        if (window.Commands) window.Commands.markUsed(item.id)
        close()
        if (item.path) {
          state.navigate(item.path)
        } else if (typeof item.action === 'function') {
          item.action()
        }
      }

      // 输入模式：提交参数执行
      const submitInput = () => {
        const cmd = pendingCmd.value
        const val = inputValue.value.trim()
        if (!cmd || !val) return
        if (window.Commands) window.Commands.markUsed(cmd.id)
        close()
        if (typeof cmd.action === 'function') cmd.action(val)
      }

      // 输入模式：Esc 返回命令列表
      const cancelInput = () => {
        inputMode.value = false
        pendingCmd.value = null
        inputValue.value = ''
        nextTick(() => { if (inputRef.value) inputRef.value.focus() })
      }

      // 键盘导航
      const onKeydown = (e) => {
        // 输入模式：Enter 提交，Esc 返回
        if (inputMode.value) {
          if (e.key === 'Enter') { e.preventDefault(); submitInput() }
          else if (e.key === 'Escape') { e.preventDefault(); cancelInput() }
          return
        }
        const items = flatItems.value
        if (items.length === 0) return
        const idx = items.findIndex(i => i.id === activeId.value)
        if (e.key === 'ArrowDown') {
          e.preventDefault()
          activeId.value = items[(idx + 1) % items.length].id
          scrollIntoView()
        } else if (e.key === 'ArrowUp') {
          e.preventDefault()
          activeId.value = items[(idx - 1 + items.length) % items.length].id
          scrollIntoView()
        } else if (e.key === 'Enter') {
          e.preventDefault()
          execute(items[idx])
        } else if (e.key === 'Escape') {
          e.preventDefault()
          close()
        }
      }

      const scrollIntoView = () => {
        nextTick(() => {
          const el = listRef.value && listRef.value.querySelector('[aria-selected="true"]')
          if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' })
        })
      }

      // 暴露 open 方法给父组件通过 ref 调用
      onMounted(() => { /* 父组件通过 ref.open() 调用 */ })
      onUnmounted(() => { if (releaseTrap) releaseTrap() })

      return { visible, query, activeId, rootRef, inputRef, listRef, groups, flatItems, open, close, execute, onKeydown, inputMode, inputValue, pendingCmd, submitInput, cancelInput, currentInput }
    },
    template: `
      <transition name="fade">
        <div v-if="visible" class="fixed inset-0 z-command flex items-start justify-center pt-[15vh] px-4" @mousedown.self="close">
          <div ref="rootRef" role="dialog" aria-modal="true" aria-label="命令面板"
               class="card w-full max-w-xl shadow-modal overflow-hidden">
            <div class="flex items-center gap-3 px-4 py-3 border-b border-border">
              <span v-if="inputMode" aria-hidden="true" class="text-brand">{{ pendingCmd?.icon || '›' }}</span>
              <span v-else aria-hidden="true" class="text-ink-muted">🔍</span>
              <span v-if="inputMode" class="text-sm text-ink-muted whitespace-nowrap">{{ pendingCmd?.label }}</span>
              <input ref="inputRef" v-model="currentInput" type="text"
                     :role="inputMode ? 'textbox' : 'combobox'"
                     :aria-expanded="!inputMode" :aria-controls="inputMode ? null : 'cmd-list'"
                     :aria-activedescendant="inputMode ? null : activeId"
                     :placeholder="inputMode ? (pendingCmd?.inputPrompt || '输入...') : '输入命令、页面名，或直接描述你想做的事...'"
                     class="flex-1 bg-transparent outline-none text-ink placeholder-ink-subtle"
                     @keydown="onKeydown" />
              <button v-if="inputMode" class="text-xs text-ink-subtle hover:text-ink" @click="cancelInput" title="返回命令列表">← 返回</button>
              <kbd v-else class="text-xs text-ink-subtle border border-border rounded px-1.5 py-0.5">ESC</kbd>
            </div>
            <ul v-if="!inputMode" ref="listRef" id="cmd-list" role="listbox" class="max-h-[50vh] overflow-y-auto py-2">
              <template v-for="(group, gi) in groups" :key="gi">
                <li role="presentation" class="px-4 py-1 text-xs font-medium text-ink-subtle uppercase tracking-wide">{{ group.label }}</li>
                <li v-for="item in group.items" :key="item.id" :id="item.id" role="option"
                    :aria-selected="item.id === activeId"
                    :class="['flex items-center gap-3 px-4 py-2 cursor-pointer text-sm transition-colors',
                             item.id === activeId ? 'bg-brand-light text-brand' : 'text-ink hover:bg-surface-muted']"
                    @mouseenter="activeId = item.id"
                    @click="execute(item)">
                  <span aria-hidden="true" class="text-base w-5 text-center">{{ item.icon || '›' }}</span>
                  <span class="flex-1">{{ item.label }}</span>
                  <kbd v-if="item.shortcut" class="text-xs text-ink-subtle border border-border rounded px-1.5 py-0.5">{{ item.shortcut }}</kbd>
                </li>
              </template>
              <li v-if="flatItems.length === 0" class="px-4 py-8 text-center text-ink-subtle text-sm">
                无匹配命令
              </li>
            </ul>
            <div v-else class="px-4 py-6 text-xs text-ink-subtle">
              输入参数后按 <kbd class="border border-border rounded px-1">↵</kbd> 执行，按 <kbd class="border border-border rounded px-1">esc</kbd> 返回命令列表
            </div>
            <footer class="flex items-center gap-4 px-4 py-2 border-t border-border text-xs text-ink-subtle bg-surface-muted">
              <template v-if="inputMode">
                <span><kbd class="border border-border rounded px-1">↵</kbd> 执行</span>
                <span><kbd class="border border-border rounded px-1">esc</kbd> 返回</span>
              </template>
              <template v-else>
                <span><kbd class="border border-border rounded px-1">↑</kbd><kbd class="border border-border rounded px-1 ml-0.5">↓</kbd> 导航</span>
                <span><kbd class="border border-border rounded px-1">↵</kbd> 执行</span>
                <span><kbd class="border border-border rounded px-1">esc</kbd> 关闭</span>
              </template>
            </footer>
          </div>
        </div>
      </transition>
    `
  }
})()
