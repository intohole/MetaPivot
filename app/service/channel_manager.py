"""ChannelManager - 启动/停止 IM 渠道适配器

职责：
1. 应用启动时根据配置注册并连接各 IM 适配器到 channel_registry
2. 应用停止时优雅断开所有适配器
3. 不感知具体适配器实现，仅做编排（依赖倒置）

依赖方向：本模块在 Service 层，向下注入 ChannelService.dispatch_message
作为适配器的 on_message 回调，使 Infra 层适配器无需向上 import Service。
"""
from app.domain.channel.adapter import channel_registry
from app.utils.config import settings
from app.utils.logger import get_logger

log = get_logger("channel_manager")


async def start_channels() -> None:
    """启动所有已启用的 IM 渠道"""
    from app.infra.channel.dingtalk import DingTalkAdapter
    from app.infra.channel.feishu import FeishuAdapter
    from app.infra.channel.wecom import WeComAdapter
    from app.service.channel_service import channel_service

    # 注入消息回调（依赖注入）：适配器收到消息后回调 channel_service.dispatch_message
    on_message = channel_service.dispatch_message

    # 钉钉
    if settings.dingtalk_enabled:
        try:
            channel_registry.register(DingTalkAdapter(), on_message=on_message)
            log.info("DingTalk adapter registered")
        except Exception as e:
            log.exception("DingTalk register failed: {}", e)

    # 飞书
    if settings.feishu_enabled:
        try:
            channel_registry.register(FeishuAdapter(), on_message=on_message)
            log.info("Feishu adapter registered")
        except Exception as e:
            log.exception("Feishu register failed: {}", e)

    # 企业微信（Webhook 模式，消息由路由层主动调用 dispatch_message）
    if settings.wecom_enabled:
        try:
            channel_registry.register(WeComAdapter())
            log.info("WeCom adapter registered")
        except Exception as e:
            log.exception("WeCom register failed: {}", e)

    # 全部异步连接
    await channel_registry.connect_all()

    channels = channel_registry.list_channels()
    if channels:
        log.info("Active channels: {}", channels)
    else:
        log.warning("No IM channel enabled, running in API-only mode")


async def stop_channels() -> None:
    """停止所有 IM 渠道"""
    try:
        await channel_registry.disconnect_all()
        log.info("All channels disconnected")
    except Exception as e:
        log.exception("Stop channels failed: {}", e)
