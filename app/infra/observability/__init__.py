"""Observability 包 - OTel + Langfuse 链路追踪

对外暴露：
- init_otel / shutdown_otel：SDK 生命周期管理
- get_tracer：获取 tracer 实例
- attach_user_baggage / detach_user_baggage：Baggage 传播
- llm_span / tool_span / agent_step_span：span contextmanager
"""
