/* ============================================================
   focus_trap.js — 纯 DOM focus trap 工具
   供 BaseModal / Drawer / Command Palette 复用
   挂载：window.trapFocus(container, options) → release()
   ============================================================ */
(function () {
  const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'

  /**
   * 在 container 内锁定键盘焦点
   * @param {HTMLElement} container - 需要锁定的容器
   * @param {Object} opts - { initialFocus: HTMLElement|'first'|'container', inertRoot: HTMLElement }
   * @returns {Function} release - 解除 trap，恢复焦点
   */
  function trapFocus(container, opts) {
    opts = opts || {}
    const previouslyFocused = document.activeElement
    const inertRoot = opts.inertRoot || document.querySelector('#app')

    // 1. 背景设 inert（屏幕阅读器不再朗读背景内容）
    if (inertRoot && inertRoot !== container && !container.contains(inertRoot)) {
      // 给 #app 的直接子节点设 inert（而非 #app 本身，避免影响 modal 自身）
      Array.from(inertRoot.children).forEach(child => {
        if (!child.contains(container) && !container.contains(child)) {
          child.inert = true
        }
      })
    }

    // 2. 收集可聚焦元素
    const getFocusable = () => Array.from(container.querySelectorAll(FOCUSABLE))
      .filter(el => el.offsetParent !== null || el === document.activeElement)

    // 3. 初始焦点
    const setInitialFocus = () => {
      const items = getFocusable()
      if (opts.initialFocus instanceof HTMLElement) {
        opts.initialFocus.focus()
      } else if (opts.initialFocus === 'first' && items.length > 0) {
        items[0].focus()
      } else if (items.length > 0) {
        items[0].focus()
      } else {
        container.setAttribute('tabindex', '-1')
        container.focus()
      }
    }
    // 延迟一帧让 Vue transition 完成
    requestAnimationFrame(setInitialFocus)

    // 4. 拦截 Tab/Shift+Tab 在 first/last 间循环
    const onKeydown = (e) => {
      if (e.key !== 'Tab') return
      const items = getFocusable()
      if (items.length === 0) { e.preventDefault(); return }
      const first = items[0]
      const last = items[items.length - 1]
      if (e.shiftKey) {
        if (document.activeElement === first || document.activeElement === container) {
          e.preventDefault()
          last.focus()
        }
      } else {
        if (document.activeElement === last) {
          e.preventDefault()
          first.focus()
        }
      }
    }
    container.addEventListener('keydown', onKeydown)

    // 5. 返回 release 函数
    let released = false
    return function release() {
      if (released) return
      released = true
      container.removeEventListener('keydown', onKeydown)
      // 解除背景 inert
      if (inertRoot) {
        Array.from(inertRoot.children).forEach(child => {
          if (child.inert) child.inert = false
        })
      }
      // 恢复焦点
      if (previouslyFocused && typeof previouslyFocused.focus === 'function') {
        try { previouslyFocused.focus() } catch (e) {}
      }
      if (container.getAttribute('tabindex') === '-1') {
        container.removeAttribute('tabindex')
      }
    }
  }

  window.trapFocus = trapFocus
})()
