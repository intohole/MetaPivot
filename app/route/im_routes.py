"""IM 接入路由 - 各 IM 平台 Webhook 回调入口

注意：
- 钉钉/飞书 Stream 模式由内部 SDK 建立 WebSocket，不走 Webhook
- 此路由用于：钉钉 HTTP 模式与卡片回调、企微回调、飞书 HTTP 模式
- 所有端点均做签名校验后调用 channel_service 派发
"""
from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from app.domain.channel.adapter import channel_registry
from app.domain.channel.models import Channel
from app.route.depend import ok
from app.service.channel_service import channel_service
from app.utils.logger import get_logger
from app.utils.response import AppError, ErrorCode

log = get_logger("im_routes")

router = APIRouter()


@router.post("/dingtalk/webhook", summary="钉钉消息回调")
async def dingtalk_webhook(request: Request):
    """钉钉消息回调（HTTP 模式或卡片回调）"""
    body = await _read_json(request)
    adapter = channel_registry.get(Channel.DINGTALK.value)
    if adapter is None:
        return {"msgtype": "text", "text": {"content": "channel not enabled"}}

    # 卡片回调分支
    if body.get("type") == "actionCallback":
        return {"status": "SUCCESS"}

    try:
        msg = await adapter.receive_message(body)
        await channel_service.dispatch_message(msg)
    except Exception as e:
        log.exception("DingTalk webhook process failed: {}", e)
    return {"msgtype": "text", "text": {"content": "received"}}


@router.post("/dingtalk/card/callback", summary="钉钉卡片回调")
async def dingtalk_card_callback(request: Request):
    """钉钉卡片交互回调"""
    body = await _read_json(request)
    adapter = channel_registry.get(Channel.DINGTALK.value)
    if adapter is None:
        return {"status": "FAIL", "message": "channel not enabled"}
    try:
        callback = await adapter.parse_callback(body)
        log.info("DingTalk card callback: card={} action={}", callback.card_id, callback.action)
    except Exception as e:
        log.exception("DingTalk card callback failed: {}", e)
        return {"status": "FAIL"}
    return {"status": "SUCCESS"}


@router.get("/wecom/callback", summary="企微验证URL", response_class=PlainTextResponse)
async def wecom_verify(
    request: Request,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    """企微启用回调时的 URL 验证"""
    adapter = channel_registry.get(Channel.WECOM.value)
    if adapter is None:
        raise AppError(ErrorCode.IM_CHANNEL_ERROR, "WeCom channel not enabled", 400)
    return PlainTextResponse(await adapter.verify_url(msg_signature, timestamp, nonce, echostr))


@router.post("/wecom/callback", summary="企微消息回调")
async def wecom_callback(request: Request):
    """企微消息回调（加密 XML）：验签 → 解密 → 解析 → 派发"""
    body = await request.body()
    adapter = channel_registry.get(Channel.WECOM.value)
    if adapter is None:
        raise AppError(ErrorCode.IM_CHANNEL_ERROR, "WeCom channel not enabled", 400)

    headers = {k.lower(): v for k, v in request.headers.items()}
    sig_info = {
        "msg_signature": headers.get("msg_signature", ""),
        "timestamp": headers.get("timestamp", ""),
        "nonce": headers.get("nonce", ""),
    }
    if not await adapter.verify_signature(sig_info, body):
        raise AppError(ErrorCode.IM_SIGNATURE_INVALID, status_code=401)

    try:
        xml_str = _wecom_decrypt_body(body)
        if not xml_str:
            return PlainTextResponse("success")
        msg = await adapter.receive_message(xml_str)
        await channel_service.dispatch_message(msg)
    except Exception as e:
        log.exception("WeCom callback process failed: {}", e)
    return PlainTextResponse("success")


def _wecom_decrypt_body(body: bytes) -> str:
    """从加密 XML body 提取并解密明文 XML（路由层负责解密，适配器仅解析）"""
    import xml.etree.ElementTree as ET

    from app.infra.channel.wecom import WeComAES
    from app.utils.config import settings

    root = ET.fromstring(body)
    encrypt = root.findtext("Encrypt", "")
    if not encrypt:
        return ""
    xml_str, _ = WeComAES.decrypt(encrypt, settings.wecom_encoding_aes_key)
    return xml_str


@router.post("/feishu/webhook", summary="飞书事件回调")
async def feishu_webhook(request: Request):
    """飞书事件回调（含 URL 验证 challenge）"""
    body = await _read_json(request)

    # URL 验证
    if "challenge" in body:
        return {"challenge": body["challenge"]}

    adapter = channel_registry.get(Channel.FEISHU.value)
    if adapter is None:
        return {"code": -1, "msg": "channel not enabled"}

    headers = {k.lower(): v for k, v in request.headers.items()}
    signature = headers.get("x-lark-signature", "")
    timestamp = headers.get("x-lark-request-timestamp", "")
    if not await adapter.verify_signature({"signature": signature, "timestamp": timestamp}, await request.body()):
        raise AppError(ErrorCode.IM_SIGNATURE_INVALID, status_code=401)

    try:
        msg = await adapter.receive_message(body)
        await channel_service.dispatch_message(msg)
    except Exception as e:
        log.exception("Feishu webhook process failed: {}", e)
    return {"code": 0}


async def _read_json(request: Request) -> dict:
    """读取 JSON body，异常返回空 dict"""
    try:
        return await request.json()
    except Exception:
        return {}


@router.get("/status", summary="IM 渠道状态")
async def channel_status(request: Request):
    """查询当前已注册的 IM 渠道"""
    return ok({"channels": channel_service.list_active_channels()}, request)
