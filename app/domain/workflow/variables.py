"""工作流变量解析 — 从 node_executors.py 抽离，供 http_node 等节点复用

支持 ${var} 占位符替换，递归处理 dict/list/str。
变量来源：context["variables"]（由 WorkflowService.execute_workflow 初始化）。
"""
import re
from typing import Any

# ${var} 占位符正则（var 名含字母/数字/下划线），单遍替换避免嵌套二次替换
_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def resolve_vars(data: Any, context: dict) -> Any:
    """递归解析 ${var} 占位符

    Args:
        data: 任意类型（dict/list/str/其他）
        context: 工作流执行上下文（含 variables 字典）

    Returns:
        解析后的数据（同结构）
    """
    if isinstance(data, str):
        return resolve_vars_str(data, context)
    if isinstance(data, dict):
        return {k: resolve_vars(v, context) for k, v in data.items()}
    if isinstance(data, list):
        return [resolve_vars(v, context) for v in data]
    return data


def resolve_vars_str(text: str, context: dict) -> str:
    """替换 ${var} 形式的变量引用（字符串场景）

    使用正则单遍替换，避免变量值含 ${other} 时被二次替换。

    Args:
        text: 含 ${var} 占位符的字符串
        context: 工作流执行上下文

    Returns:
        替换后的字符串；无 ${ 则原样返回；未匹配的 ${var} 保留原样
    """
    if not text or "${" not in text:
        return text
    variables = context.get("variables", {})

    def _replace(match: re.Match) -> str:
        key = match.group(1)
        if key in variables:
            return str(variables[key])
        return match.group(0)  # 未匹配的 ${var} 原样保留

    return _VAR_PATTERN.sub(_replace, text)