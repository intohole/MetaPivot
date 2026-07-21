/* 登录页 — 左登录 + 右品牌介绍（Linear 风格分屏布局）
 * Slogan: "描述即执行，沉淀即复用" — 传递 Agent 执行 + Skill 沉淀双核心价值
 */
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
          // 按角色分流：tenant_admin/tenant_manager → 管理端，user → 客户端
          const isAdmin = ['tenant_admin', 'tenant_manager', 'platform_admin'].includes(data.user.role)
          if (isAdmin) {
            window.location.href = '/admin#/dashboard'
          } else {
            state.navigate('/dashboard')
          }
        } catch (e) {
          // API 层已统一 Toast，此处仅重置密码
          form.password = ''
        } finally {
          submitting.value = false
        }
      }

      const onKey = (e) => { if (e.key === 'Enter') handleSubmit() }

      const fillDemo = (u, p) => {
        form.username = u
        form.password = p
      }

      // 品牌介绍区核心价值点（3C 框架推导）
      const valuePoints = [
        { icon: '🤖', title: 'Agent 优先', desc: '描述需求即执行，告别配置工作流的门槛' },
        { icon: '🧩', title: 'Skill 自进化', desc: '任务自动沉淀为可复用 Skill，团队经验越用越多' },
        { icon: '⚡', title: '工作流引擎', desc: 'DAG 可视化编排，定时 / Webhook / IM 多触发方式' },
        { icon: '💬', title: 'IM 原生', desc: '钉钉 / 企微 / 飞书双向打通，IM 消息即触发' }
      ]

      // 演示账号快捷登录（角色对比，便于深度测试）
      const demoAccounts = [
        { label: '管理员', username: 'admin', password: 'admin123', desc: '全功能' },
        { label: '经理', username: 'manager', password: 'manager123', desc: '审计+工作台' },
        { label: '普通用户', username: 'user', password: 'user123', desc: '工作台' }
      ]

      return { form, errors, submitting, handleSubmit, onKey, fillDemo, valuePoints, demoAccounts }
    },
    template: `
      <div class="min-h-screen flex">
        <!-- 左侧：登录表单（Linear 风格 — 浅色画布 + 卡片）-->
        <div class="flex-1 flex items-center justify-center bg-surface-canvas px-6 py-12 relative">
          <!-- 移动端顶栏 Logo（桌面端隐藏）-->
          <div class="absolute top-6 left-6 md:hidden flex items-center gap-2">
            <span class="text-2xl" aria-hidden="true">🤖</span>
            <span class="font-bold text-ink">MetaPivot</span>
          </div>

          <div class="w-full max-w-sm">
            <!-- 桌面端 Logo（移动端隐藏，因右侧介绍区已有大 Logo）-->
            <div class="hidden md:block mb-10">
              <div class="flex items-center gap-2 mb-2">
                <span class="text-3xl" aria-hidden="true">🤖</span>
                <span class="font-bold text-xl text-ink">MetaPivot</span>
              </div>
              <p class="text-sm text-ink-subtle">欢迎回来，请登录继续</p>
            </div>

            <form @submit.prevent="handleSubmit" class="space-y-5" novalidate>
              <div>
                <label for="username" class="block text-sm font-medium text-ink mb-1.5">用户名</label>
                <input id="username" type="text" v-model="form.username"
                       :class="['input', errors.username ? 'border-danger' : '']"
                       :aria-invalid="errors.username ? 'true' : 'false'"
                       aria-describedby="username-err"
                       placeholder="请输入用户名" autocomplete="username" @keydown="onKey" />
                <p v-if="errors.username" id="username-err" class="mt-1.5 text-xs text-danger" role="alert">{{ errors.username }}</p>
              </div>

              <div>
                <label for="password" class="block text-sm font-medium text-ink mb-1.5">密码</label>
                <input id="password" type="password" v-model="form.password"
                       :class="['input', errors.password ? 'border-danger' : '']"
                       :aria-invalid="errors.password ? 'true' : 'false'"
                       aria-describedby="password-err"
                       placeholder="请输入密码" autocomplete="current-password" @keydown="onKey" />
                <p v-if="errors.password" id="password-err" class="mt-1.5 text-xs text-danger" role="alert">{{ errors.password }}</p>
              </div>

              <button type="submit" class="btn btn-primary w-full justify-center" :disabled="submitting"
                      :aria-busy="submitting">
                <span v-if="submitting" class="inline-block animate-spin mr-2" aria-hidden="true">⟳</span>
                {{ submitting ? '登录中...' : '登录' }}
              </button>
            </form>

            <!-- 演示账号快捷登录（便于深度测试角色权限）-->
            <div class="mt-8 pt-6 border-t border-border">
              <p class="text-xs font-medium text-ink-subtle uppercase tracking-wider mb-3">演示账号（点击填充）</p>
              <div class="grid grid-cols-3 gap-2">
                <button v-for="acc in demoAccounts" :key="acc.username" type="button"
                        @click="fillDemo(acc.username, acc.password)"
                        class="card p-2.5 text-left hover:border-brand hover:bg-brand-light transition-colors cursor-pointer"
                        :title="acc.username + ' / ' + acc.password + ' (' + acc.desc + ')'">
                  <p class="text-xs font-semibold text-ink">{{ acc.label }}</p>
                  <p class="text-[11px] text-ink-subtle mt-0.5">{{ acc.desc }}</p>
                </button>
              </div>
              <p class="mt-3 text-[11px] text-ink-subtle">提示：admin 全功能 / manager 审计+工作台 / user 仅工作台</p>
            </div>
          </div>
        </div>

        <!-- 右侧：品牌介绍区（Linear 风格 — 品牌色渐变背景 + 价值主张）-->
        <div class="hidden lg:flex lg:w-[56%] xl:w-[58%] bg-gradient-to-br from-brand via-brand-dark to-indigo-700 relative overflow-hidden">
          <!-- 装饰性几何图形（Linear 风格的克制装饰）-->
          <div class="absolute inset-0 opacity-20" aria-hidden="true">
            <div class="absolute -top-24 -right-24 w-96 h-96 rounded-full bg-white/10 blur-3xl"></div>
            <div class="absolute -bottom-32 -left-32 w-96 h-96 rounded-full bg-indigo-300/20 blur-3xl"></div>
            <div class="absolute top-1/2 right-1/4 w-64 h-64 rounded-full bg-brand-hover/30 blur-3xl"></div>
          </div>

          <!-- 网格点装饰（Linear 标志性）-->
          <div class="absolute inset-0 opacity-[0.08]" aria-hidden="true"
               style="background-image: radial-gradient(circle, white 1px, transparent 1px); background-size: 28px 28px;"></div>

          <div class="relative z-10 flex flex-col justify-center px-16 xl:px-24 py-12 text-white w-full">
            <!-- 品牌 Logo + 名称 -->
            <div class="flex items-center gap-3 mb-12">
              <span class="text-4xl" aria-hidden="true">🤖</span>
              <span class="text-2xl font-semibold" style="letter-spacing: -0.02em;">MetaPivot</span>
            </div>

            <!-- 主 Slogan -->
            <h1 class="text-5xl xl:text-6xl font-semibold leading-tight" style="letter-spacing: -0.03em;">
              描述即执行<br />
              沉淀即复用
            </h1>

            <!-- 副标题 -->
            <p class="mt-6 text-lg text-white/80 leading-relaxed max-w-md">
              企业 IM 自动化办公平台 — 让 Agent 干活，让 Skill 沉淀，让 IM 触发
            </p>

            <!-- 核心价值点（3C 框架推导的差异化）-->
            <div class="mt-12 grid grid-cols-2 gap-x-8 gap-y-6 max-w-xl">
              <div v-for="vp in valuePoints" :key="vp.title" class="flex items-start gap-3">
                <div class="flex-shrink-0 w-10 h-10 rounded-lg bg-white/15 backdrop-blur-sm flex items-center justify-center text-xl"
                     aria-hidden="true">{{ vp.icon }}</div>
                <div class="flex-1 min-w-0">
                  <p class="text-sm font-semibold text-white">{{ vp.title }}</p>
                  <p class="text-xs text-white/70 mt-1 leading-relaxed">{{ vp.desc }}</p>
                </div>
              </div>
            </div>

            <!-- 底部信任背书 -->
            <div class="absolute bottom-12 left-16 xl:left-24 right-16 xl:right-24 pt-6 border-t border-white/15">
              <div class="flex items-center justify-between text-xs text-white/60">
                <span>支持钉钉 · 企微 · 飞书</span>
                <span>SQLite · PostgreSQL · Redis</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    `
  }
})()
