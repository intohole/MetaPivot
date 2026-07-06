/* ============================================================
   overlay.js — Dropdown 下拉菜单 + Tooltip 悬浮提示
   挂载：window.Components.DropdownMenu / window.Components.Tooltip
   ============================================================ */
(function () {
  const { ref, onMounted, onUnmounted, nextTick } = Vue
  const Components = window.Components || (window.Components = {})

  /* --- Dropdown 下拉菜单 ---
   * 用法：<dropdown-menu :items="[{label,icon,action,danger,divider}]" />
   * 触发：默认 slot 为触发元素；无 slot 时用 ⋯ 图标
   */
  Components.DropdownMenu = {
    name: 'DropdownMenu',
    props: {
      items: { type: Array, required: true },
      align: { type: String, default: 'right' }  // left | right
    },
    setup(props, { slots }) {
      const open = ref(false)
      const rootRef = ref(null)
      const activeIdx = ref(-1)

      const toggle = () => {
        open.value = !open.value
        if (open.value) activeIdx.value = props.items.findIndex(i => !i.divider)
      }
      const close = () => { open.value = false; activeIdx.value = -1 }

      const executeItem = (item) => {
        if (item.divider || item.disabled) return
        close()
        if (typeof item.action === 'function') item.action()
      }

      const onKeydown = (e) => {
        if (!open.value) return
        const validIndices = props.items.map((it, i) => it.divider ? -1 : i).filter(i => i >= 0)
        const currentPos = validIndices.indexOf(activeIdx.value)
        if (e.key === 'ArrowDown') {
          e.preventDefault()
          activeIdx.value = validIndices[(currentPos + 1) % validIndices.length]
        } else if (e.key === 'ArrowUp') {
          e.preventDefault()
          activeIdx.value = validIndices[(currentPos - 1 + validIndices.length) % validIndices.length]
        } else if (e.key === 'Enter') {
          e.preventDefault()
          executeItem(props.items[activeIdx.value])
        } else if (e.key === 'Escape') {
          e.preventDefault()
          close()
        }
      }

      const onClickOutside = (e) => {
        if (rootRef.value && !rootRef.value.contains(e.target)) close()
      }

      onMounted(() => {
        document.addEventListener('click', onClickOutside)
        document.addEventListener('keydown', onKeydown)
      })
      onUnmounted(() => {
        document.removeEventListener('click', onClickOutside)
        document.removeEventListener('keydown', onKeydown)
      })

      return { open, rootRef, activeIdx, toggle, close, executeItem, slots }
    },
    template: `
      <div ref="rootRef" class="relative inline-block">
        <button v-if="!slots || !slots.default" @click="toggle" :aria-expanded="open" aria-haspopup="menu"
                class="btn btn-ghost p-1.5" aria-label="更多操作">
          <span aria-hidden="true">⋯</span>
        </button>
        <span v-else @click="toggle"><slot /></span>
        <transition name="fade">
          <ul v-if="open" role="menu"
              :class="['absolute z-dropdown mt-1 min-w-[160px] card shadow-modal py-1',
                       align === 'left' ? 'left-0' : 'right-0']">
            <template v-for="(item, idx) in items" :key="idx">
              <li v-if="item.divider" role="separator" class="my-1 border-t border-border"></li>
              <li v-else role="menuitem"
                  :class="['flex items-center gap-2 px-3 py-1.5 text-sm cursor-pointer transition-colors',
                           item.danger ? 'text-danger hover:bg-danger-bg' : 'text-ink hover:bg-surface-muted',
                           idx === activeIdx ? 'bg-surface-muted' : '']"
                  @click="executeItem(item)" @mouseenter="activeIdx = idx">
                <span v-if="item.icon" aria-hidden="true" class="w-4 text-center">{{ item.icon }}</span>
                <span class="flex-1">{{ item.label }}</span>
                <kbd v-if="item.shortcut" class="text-xs text-ink-subtle">{{ item.shortcut }}</kbd>
              </li>
            </template>
          </ul>
        </transition>
      </div>
    `
  }

  /* --- Tooltip 悬浮提示 ---
   * 用法：<tooltip text="说明" placement="top"><button>?</button></tooltip>
   * 鼠标 hover + 键盘 focus 都触发
   */
  Components.Tooltip = {
    name: 'Tooltip',
    props: {
      text: { type: String, required: true },
      placement: { type: String, default: 'top' }  // top | bottom | left | right
    },
    setup() {
      const visible = ref(false)
      const show = () => { visible.value = true }
      const hide = () => { visible.value = false }
      return { visible, show, hide }
    },
    template: `
      <span class="relative inline-flex" @mouseenter="show" @mouseleave="hide" @focusin="show" @focusout="hide">
        <slot />
        <transition name="fade">
          <span v-if="visible && text" role="tooltip"
                :class="['absolute z-dropdown px-2 py-1 text-xs text-white bg-gray-900 rounded whitespace-nowrap pointer-events-none',
                         placement === 'top' ? 'bottom-full mb-1 left-1/2 -translate-x-1/2' : '',
                         placement === 'bottom' ? 'top-full mt-1 left-1/2 -translate-x-1/2' : '',
                         placement === 'left' ? 'right-full mr-1 top-1/2 -translate-y-1/2' : '',
                         placement === 'right' ? 'left-full ml-1 top-1/2 -translate-y-1/2' : '']">
            {{ text }}
          </span>
        </transition>
      </span>
    `
  }
})()
