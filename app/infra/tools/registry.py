"""函数工具注册表 - 管理 source_type=function 的 Skill 调用

设计：
- Skill.source_ref 形如 "app.infra.tools.time_tool.get_time"
- 通过 importlib 动态加载模块并获取函数
- 函数签名：async def fn(args: dict) -> dict
"""
import importlib
from typing import Any, Callable, Optional

from app.utils.logger import get_logger

log = get_logger("tool_registry")

# 已加载函数缓存（避免每次反射）
_fn_cache: dict[str, Callable] = {}


def load_function(source_ref: str) -> Callable:
    """根据 source_ref 加载 Python 函数

    source_ref 格式：module.path.function_name
    例：app.infra.tools.time_tool.get_time
    """
    if source_ref in _fn_cache:
        return _fn_cache[source_ref]

    parts = source_ref.rsplit(".", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid source_ref: {source_ref}")

    module_path, fn_name = parts
    try:
        module = importlib.import_module(module_path)
        fn = getattr(module, fn_name)
        if not callable(fn):
            raise TypeError(f"{source_ref} is not callable")
        _fn_cache[source_ref] = fn
        log.debug("Loaded function: {}", source_ref)
        return fn
    except ImportError as e:
        log.error("Module not found: {} - {}", module_path, e)
        raise
    except AttributeError as e:
        log.error("Function not found: {} in {}", fn_name, module_path)
        raise


async def call_function(source_ref: str, args: dict) -> dict:
    """调用函数工具

    支持同步与异步函数（自动适配）。
    返回值统一为 dict（若函数返回非 dict 则包装为 {"result": value}）。
    """
    import asyncio

    fn = load_function(source_ref)
    try:
        if asyncio.iscoroutinefunction(fn):
            result = await fn(args)
        else:
            # 同步函数放到线程池避免阻塞事件循环
            result = await asyncio.to_thread(fn, args)
        if isinstance(result, dict):
            return result
        return {"result": result}
    except Exception as e:
        log.exception("Function call failed: {} - {}", source_ref, e)
        return {"error": str(e)}
