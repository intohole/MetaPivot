/* ============================================================
   form.js — Switch 开关 + TagInput 标签输入
   挂载：window.Components.Switch / window.Components.TagInput
   ============================================================ */
(function () {
  const { ref } = Vue
  const Components = window.Components || (window.Components = {})

  /* --- Switch 开关 ---
   * 用法：<switch v-model="enabled" label="启用" />
   * ARIA: role="switch" + aria-checked，键盘 Space/Toggle
   */
  Components.Switch = {
    name: 'Switch',
    props: {
      modelValue: { type: Boolean, default: false },
      label: String,
      disabled: { type: Boolean, default: false },
      size: { type: String, default: 'md' }  // sm | md
    },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      const toggle = () => {
        if (!props.disabled) emit('update:modelValue', !props.modelValue)
      }
      const onKeydown = (e) => {
        if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); toggle() }
      }
      return { toggle, onKeydown }
    },
    template: `
      <label class="inline-flex items-center gap-2" :class="disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer'">
        <button type="button" role="switch" :aria-checked="modelValue ? 'true' : 'false'"
                :aria-label="label" :disabled="disabled" @click="toggle" @keydown="onKeydown"
                :class="['relative inline-flex flex-shrink-0 rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-brand focus:ring-offset-2',
                         size === 'sm' ? 'h-4 w-7' : 'h-6 w-11',
                         modelValue ? 'bg-brand' : 'bg-gray-300']">
          <span aria-hidden="true"
                :class="['pointer-events-none inline-block bg-white rounded-full shadow transform transition-transform',
                         size === 'sm' ? 'h-3 w-3' : 'h-5 w-5',
                         modelValue ? (size === 'sm' ? 'translate-x-3.5' : 'translate-x-5') : 'translate-x-0.5']"></span>
        </button>
        <span v-if="label" class="text-sm text-ink">{{ label }}</span>
      </label>
    `
  }

  /* --- TagInput 标签输入 ---
   * 用法：<tag-input v-model="tags" placeholder="输入回车添加" :max="10" />
   * 键盘：Enter 添加 / Backspace 删除最后一个 / 点击 × 删除
   */
  Components.TagInput = {
    name: 'TagInput',
    props: {
      modelValue: { type: Array, default: () => [] },
      placeholder: { type: String, default: '输入后回车添加' },
      max: { type: Number, default: 0 },  // 0 = 不限
      label: String,
      color: { type: String, default: 'brand' }  // brand | gray
    },
    emits: ['update:modelValue'],
    setup(props, { emit }) {
      const inputValue = ref('')
      const atMax = () => props.max > 0 && props.modelValue.length >= props.max
      const addTag = () => {
        const v = inputValue.value.trim()
        if (!v || atMax()) { inputValue.value = ''; return }
        if (props.modelValue.includes(v)) { inputValue.value = ''; return }
        emit('update:modelValue', [...props.modelValue, v])
        inputValue.value = ''
      }
      const removeTag = (idx) => {
        const next = [...props.modelValue]
        next.splice(idx, 1)
        emit('update:modelValue', next)
      }
      const onKeydown = (e) => {
        if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); addTag() }
        else if (e.key === 'Backspace' && !inputValue.value && props.modelValue.length > 0) {
          removeTag(props.modelValue.length - 1)
        }
      }
      return { inputValue, addTag, removeTag, onKeydown, atMax }
    },
    template: `
      <div class="space-y-1">
        <label v-if="label" class="block text-sm font-medium text-ink">{{ label }}</label>
        <div class="flex flex-wrap items-center gap-1.5 p-2 border border-border rounded bg-surface focus-within:ring-2 focus-within:ring-brand"
             :class="{ 'cursor-not-allowed opacity-60': atMax() }">
          <span v-for="(tag, idx) in modelValue" :key="idx"
                :class="['inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded',
                         color === 'gray' ? 'bg-surface-muted text-ink-muted' : 'bg-brand-light text-brand']">
            {{ tag }}
            <button type="button" @click="removeTag(idx)"
                    class="hover:text-danger" :aria-label="'删除标签 ' + tag">×</button>
          </span>
          <input v-model="inputValue" type="text" :placeholder="atMax() ? '已达上限' : placeholder"
                 :disabled="atMax()" @keydown="onKeydown" @blur="addTag"
                 class="flex-1 min-w-[120px] bg-transparent outline-none text-sm text-ink placeholder-ink-subtle" />
        </div>
        <p v-if="max > 0" class="text-xs text-ink-subtle">{{ modelValue.length }} / {{ max }}</p>
      </div>
    `
  }
})()
