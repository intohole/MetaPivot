/* 登录页 — 全屏表单，调用 /auth/token 获取 JWT */
(function () {
  const { ref, reactive } = Vue
  window.Pages = window.Pages || {}

  window.Pages.Login = {
    name: 'LoginPage',
    setup() {
      const state = window.AppState
      const form = reactive({ username: '', password: '' })
      const errors = reactive({ username: '', password: '' })
      const submitting = ref(false)

      const validate = () => {
        errors.username = form.username ? '' : '请输入用户名'
        errors.password = form.password ? '' : '请输入密码'
        return !errors.username && !errors.password
      }

      const handleSubmit = async () => {
        if (!validate() || submitting.value) return
        submitting.value = true
        try {
          const data = await window.API.post('/auth/token', {
            username: form.username, password: form.password
          })
          state.setAuth(data.token, data.user)
          state.notify('登录成功，欢迎回来 ' + data.user.username, 'success')
          state.navigate('/dashboard')
        } catch (e) {
          // API 层已统一 Toast，此处仅重置密码
          form.password = ''
        } finally {
          submitting.value = false
        }
      }

      const onKey = (e) => { if (e.key === 'Enter') handleSubmit() }

      return { form, errors, submitting, handleSubmit, onKey }
    },
    template: `
      <div class="min-h-screen flex items-center justify-center bg-gradient-to-br from-blue-50 via-surface to-indigo-50 p-4">
        <div class="card w-full max-w-md p-8 shadow-md">
          <div class="text-center mb-8">
            <div class="text-5xl mb-3" aria-hidden="true">🤖</div>
            <h1 class="text-2xl font-bold text-ink">MetaPivot</h1>
            <p class="mt-1 text-sm text-ink-muted">企业 IM 自动化办公服务</p>
          </div>

          <form @submit.prevent="handleSubmit" class="space-y-4" novalidate>
            <div>
              <label for="username" class="block text-sm font-medium text-ink mb-1">用户名</label>
              <input id="username" type="text" v-model="form.username"
                     :class="['input', errors.username ? 'border-danger' : '']"
                     :aria-invalid="errors.username ? 'true' : 'false'"
                     aria-describedby="username-err"
                     placeholder="请输入用户名" autocomplete="username" @keydown="onKey" />
              <p v-if="errors.username" id="username-err" class="mt-1 text-xs text-danger" role="alert">{{ errors.username }}</p>
            </div>

            <div>
              <label for="password" class="block text-sm font-medium text-ink mb-1">密码</label>
              <input id="password" type="password" v-model="form.password"
                     :class="['input', errors.password ? 'border-danger' : '']"
                     :aria-invalid="errors.password ? 'true' : 'false'"
                     aria-describedby="password-err"
                     placeholder="请输入密码" autocomplete="current-password" @keydown="onKey" />
              <p v-if="errors.password" id="password-err" class="mt-1 text-xs text-danger" role="alert">{{ errors.password }}</p>
            </div>

            <button type="submit" class="btn btn-primary w-full" :disabled="submitting"
                    :aria-busy="submitting">
              <span v-if="submitting" class="inline-block animate-spin">⟳</span>
              {{ submitting ? '登录中...' : '登录' }}
            </button>
          </form>

          <div class="mt-6 pt-4 border-t border-border text-center">
            <p class="text-xs text-ink-subtle">默认管理员账号 admin / admin123</p>
          </div>
        </div>
      </div>
    `
  }
})()
