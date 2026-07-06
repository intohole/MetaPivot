/* ============================================================
   navigation.js — Tabs 标签页 + Breadcrumb 面包屑
   挂载：window.Components.Tabs / window.Components.Breadcrumb
   ============================================================ */
(function () {
  const { computed } = Vue
  const Components = window.Components || (window.Components = {})

  /* --- Tabs 标签页 ---
   * 用法：<tabs v-model="active" :tabs="[{key,label,icon}]" />
   * ARIA: role="tablist" + role="tab" + aria-selected
   */
  Components.Tabs = {
    name: 'Tabs',
    props: {
      modelValue: { type: String, default: '' },
      tabs: { type: Array, required: true }  // [{key, label, icon?}]
    },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      const current = computed({
        get: () => props.modelValue || (props.tabs[0] && props.tabs[0].key) || '',
        set: (v) => emit('update:modelValue', v)
      })
      const select = (key) => { current.value = key }
      return { current, select }
    },
    template: `
      <div role="tablist" class="flex items-center gap-1 border-b border-border">
        <button v-for="tab in tabs" :key="tab.key"
                role="tab" :aria-selected="current === tab.key"
                :class="['px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px',
                         current === tab.key ? 'border-brand text-brand' : 'border-transparent text-ink-muted hover:text-ink hover:border-border']"
                @click="select(tab.key)">
          <span v-if="tab.icon" aria-hidden="true" class="mr-1">{{ tab.icon }}</span>
          {{ tab.label }}
        </button>
      </div>
    `
  }

  /* --- Breadcrumb 面包屑 ---
   * 用法：<breadcrumb :items="[{label, path?}]" />
   * 最后一项不可点击（当前页）
   */
  Components.Breadcrumb = {
    name: 'Breadcrumb',
    props: {
      items: { type: Array, required: true }  // [{label, path?}]
    },
    setup(props) {
      const state = window.AppState
      const go = (item) => { if (item.path) state.navigate(item.path) }
      return { go }
    },
    template: `
      <nav aria-label="面包屑" class="flex items-center gap-1 text-sm text-ink-muted">
        <template v-for="(item, idx) in items" :key="idx">
          <span v-if="idx > 0" aria-hidden="true" class="text-ink-subtle">/</span>
          <span v-if="idx === items.length - 1" class="text-ink font-medium" aria-current="page">{{ item.label }}</span>
          <button v-else @click="go(item)"
                  class="hover:text-brand transition-colors px-0.5"
                  :class="item.path ? 'cursor-pointer' : 'cursor-default'">
            {{ item.label }}
          </button>
        </template>
      </nav>
    `
  }
})()
