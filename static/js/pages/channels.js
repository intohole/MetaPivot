/* IM 渠道配置 — 钉钉/企微/飞书 配置 + 状态查看 */
(function () {
  const { ref, reactive, onMounted, computed } = Vue
  window.Pages = window.Pages || {}

  window.Pages.Channels = {
    name: 'ChannelsPage',
    setup() {
      const state = window.AppState
      const channels = ref([])
      const loading = ref(false)

      const showConfig = ref(false)
      const editingChannel = ref('')
      const form = reactive({})

      const loadList = async () => {
        loading.value = true
        try {
          const res = await window.API.get('/im/status')
          channels.value = res.channels || []
        } catch (e) {
          // 后端 IM 状态接口可能未启用，使用本地默认
          channels.value = [
            { channel: 'dingtalk', name: '钉钉', enabled: false },
            { channel: 'wecom', name: '企业微信', enabled: false },
            { channel: 'feishu', name: '飞书', enabled: false }
          ]
        } finally { loading.value = false }
      }

      const channelMeta = {
        dingtalk: {
          name: '钉钉', icon: '💬', color: 'bg-blue-50 text-blue-700',
          fields: [
            { key: 'dingtalk_client_id', label: 'Client ID', type: 'text' },
            { key: 'dingtalk_client_secret', label: 'Client Secret', type: 'password' },
            { key: 'dingtalk_enabled', label: '启用', type: 'boolean' }
          ]
        },
        wecom: {
          name: '企业微信', icon: '🏢', color: 'bg-green-50 text-green-700',
          fields: [
            { key: 'wecom_corp_id', label: 'Corp ID', type: 'text' },
            { key: 'wecom_app_secret', label: 'App Secret', type: 'password' },
            { key: 'wecom_agent_id', label: 'Agent ID', type: 'text' },
            { key: 'wecom_token', label: 'Token', type: 'text' },
            { key: 'wecom_encoding_aes_key', label: 'EncodingAESKey', type: 'text' },
            { key: 'wecom_enabled', label: '启用', type: 'boolean' }
          ]
        },
        feishu: {
          name: '飞书', icon: '🐦', color: 'bg-purple-50 text-purple-700',
          fields: [
            { key: 'feishu_app_id', label: 'App ID', type: 'text' },
            { key: 'feishu_app_secret', label: 'App Secret', type: 'password' },
            { key: 'feishu_enabled', label: '启用', type: 'boolean' }
          ]
        }
      }

      const openConfig = (channel) => {
        editingChannel.value = channel
        const meta = channelMeta[channel]
        Object.keys(form).forEach(k => delete form[k])
        meta.fields.forEach(f => {
          form[f.key] = f.type === 'boolean' ? false : ''
        })
        showConfig.value = true
      }

      const submitConfig = async () => {
        try {
          // 通过 /configs 接口逐项保存（IM 配置归类为 im 类目）
          const promises = Object.entries(form).map(([key, value]) =>
            window.API.put('/configs/' + key, { value: String(value) })
          )
          await Promise.all(promises)
          state.notify(editingChannel.value + ' 配置已保存（重启后生效）', 'success')
          showConfig.value = false
          loadList()
        } catch (e) {}
      }

      onMounted(loadList)

      return {
        channels, loading, channelMeta, showConfig, editingChannel, form,
        loadList, openConfig, submitConfig, state
      }
    },
    template: `
      <div class="space-y-4">
        <base-card title="IM 渠道" subtitle="配置钉钉/企微/飞书接入凭证，启用后服务自动接入消息">
          <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div v-for="ch in (channels.length ? channels : [{channel:'dingtalk'},{channel:'wecom'},{channel:'feishu'}])" :key="ch.channel"
                 class="card p-5">
              <div class="flex items-center justify-between mb-3">
                <div class="flex items-center gap-3">
                  <div :class="['w-12 h-12 rounded-lg flex items-center justify-center text-2xl', channelMeta[ch.channel]?.color]"
                       aria-hidden="true">{{ channelMeta[ch.channel]?.icon }}</div>
                  <div>
                    <p class="font-semibold text-ink">{{ channelMeta[ch.channel]?.name }}</p>
                    <p class="text-xs text-ink-muted">{{ ch.channel }}</p>
                  </div>
                </div>
                <span :class="['badge', ch.enabled ? 'badge-success' : 'badge-muted']">
                  {{ ch.enabled ? '已启用' : '未启用' }}
                </span>
              </div>
              <div class="text-xs text-ink-muted mb-3">
                <p v-if="ch.connected_at">接入时间：{{ ch.connected_at }}</p>
                <p v-if="ch.message_count !== undefined">消息数：{{ ch.message_count }}</p>
              </div>
              <button class="btn btn-secondary w-full text-sm" @click="openConfig(ch.channel)">⚙️ 配置</button>
            </div>
          </div>
        </base-card>

        <base-card title="Webhook 回调地址" subtitle="在 IM 平台配置以下回调地址接收消息">
          <div class="space-y-2">
            <div class="flex items-center justify-between p-3 bg-surface-muted rounded-md">
              <div>
                <p class="text-sm font-medium text-ink">钉钉</p>
                <p class="text-xs text-ink-muted font-mono">POST /api/v1/im/dingtalk/webhook</p>
              </div>
              <span class="badge badge-info">HTTP / Stream</span>
            </div>
            <div class="flex items-center justify-between p-3 bg-surface-muted rounded-md">
              <div>
                <p class="text-sm font-medium text-ink">企业微信</p>
                <p class="text-xs text-ink-muted font-mono">GET/POST /api/v1/im/wecom/callback</p>
              </div>
              <span class="badge badge-info">XML 加密</span>
            </div>
            <div class="flex items-center justify-between p-3 bg-surface-muted rounded-md">
              <div>
                <p class="text-sm font-medium text-ink">飞书</p>
                <p class="text-xs text-ink-muted font-mono">POST /api/v1/im/feishu/webhook</p>
              </div>
              <span class="badge badge-info">事件订阅</span>
            </div>
          </div>
        </base-card>

        <!-- 配置弹窗 -->
        <base-modal v-model="showConfig" :title="channelMeta[editingChannel]?.name + ' 配置'" width="max-w-lg">
          <div class="space-y-3">
            <div v-for="f in (channelMeta[editingChannel]?.fields || [])" :key="f.key">
              <label :for="'cfg-' + f.key" class="block text-sm font-medium text-ink mb-1">{{ f.label }}</label>
              <input v-if="f.type === 'text'" :id="'cfg-' + f.key" type="text" v-model="form[f.key]" class="input" />
              <input v-else-if="f.type === 'password'" :id="'cfg-' + f.key" type="password" v-model="form[f.key]" class="input" autocomplete="off" />
              <label v-else-if="f.type === 'boolean'" class="flex items-center gap-2 h-[38px]">
                <input type="checkbox" v-model="form[f.key]" />
                <span class="text-sm text-ink">启用此渠道</span>
              </label>
            </div>
          </div>
          <template #footer>
            <button class="btn btn-secondary" @click="showConfig = false">取消</button>
            <button class="btn btn-primary" @click="submitConfig">保存配置</button>
          </template>
        </base-modal>
      </div>
    `
  }
})()
