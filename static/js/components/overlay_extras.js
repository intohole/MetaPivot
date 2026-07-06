/* ============================================================
   overlay_extras.js — Drawer 抽屉（侧滑面板）
   挂载：window.Components.Drawer
   复用 focus_trap.js，适合详情/编辑/批量操作等重场景
   ============================================================ */
(function () {
  const { ref, watch, nextTick, onMounted, onUnmounted } = Vue
  const Components = window.Components || (window.Components = {})

  /* --- Drawer 抽屉 ---
   * 用法：<drawer v-model="open" title="详情" side="right" width="max-w-lg">内容</drawer>
   * side: left | right；focus trap + 背景 inert + Esc 关闭
   */
  Components.Drawer = {
    name: 'Drawer',
    props: {
      modelValue: { type: Boolean, default: false },
      title: String,
      side: { type: String, default: 'right' },  // left | right
      width: { type: String, default: 'max-w-md' }
    },
    emits: ['update:modelValue', 'close'],
    setup(props, { emit }) {
      const rootRef = ref(null)
      let releaseTrap = null
      const close = () => { emit('update:modelValue', false); emit('close') }
      // focus trap：打开时锁定 + 背景设 inert
      watch(() => props.modelValue, async (v) => {
        if (v) {
          await nextTick()
          if (rootRef.value && window.trapFocus) releaseTrap = window.trapFocus(rootRef.value)
        } else if (releaseTrap) {
          releaseTrap()
          releaseTrap = null
        }
      })
      const onKey = (e) => { if (e.key === 'Escape' && props.modelValue) close() }
      onMounted(() => document.addEventListener('keydown', onKey))
      onUnmounted(() => {
        document.removeEventListener('keydown', onKey)
        if (releaseTrap) releaseTrap()
      })
      return { close, rootRef }
    },
    template: `
      <transition name="drawer-fade">
        <div v-if="modelValue" class="fixed inset-0 z-modal" role="dialog" aria-modal="true" :aria-label="title">
          <div class="absolute inset-0 bg-black/50" @click="close" aria-hidden="true"></div>
          <div ref="rootRef"
               :class="['absolute top-0 bottom-0 flex flex-col bg-surface shadow-modal',
                        side === 'left' ? 'left-0' : 'right-0', width, side === 'left' ? 'drawer-slide-left' : 'drawer-slide-right']">
            <header class="flex items-center justify-between px-6 py-4 border-b border-border flex-shrink-0">
              <h3 class="text-lg font-semibold text-ink">{{ title }}</h3>
              <button @click="close" class="text-ink-subtle hover:text-ink text-2xl leading-none" aria-label="关闭">×</button>
            </header>
            <div class="flex-1 px-6 py-4 overflow-y-auto"><slot /></div>
            <footer v-if="$slots.footer" class="px-6 py-4 border-t border-border flex justify-end gap-2 flex-shrink-0">
              <slot name="footer" />
            </footer>
          </div>
        </div>
      </transition>
    `
  }
})()
