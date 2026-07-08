"""IVerifier - Agent 结果验证器契约

Phase 4.2: 渐进增强 — 在 Agent 执行完成后、生成最终回复前，
对结果质量做独立验证（与 Judge/Reflector 互补）：

- Judge（L4）: 执行中每 K 步评估，决定 continue/stop/failed（终止控制）
- Reflector（L3）: 执行中工具失败/循环时反思，决定 complete/continue/give_up/replan（动作选择）
- Verifier（Phase 4.2）: 执行后、回复前验证，决定 verified/needs_revision/failed（结果质量）

验证维度：
1. 相关性：结果是否回答了用户原始问题
2. 完整性：是否遗漏关键信息
3. 准确性：是否与工具返回的事实一致（防幻觉）

输出 VerifyResult，graph.py 据此决定是否进入 reply 或标记 FAILED。
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable


class VerifyDecision(str, Enum):
    """验证决策枚举"""
    VERIFIED = "verified"              # 结果可信，进入 reply
    NEEDS_REVISION = "needs_revision"  # 结果有瑕疵，reply 时附加 caveat 提示
    FAILED = "failed"                  # 结果错误/幻觉，标记 FAILED


@dataclass
class VerifyResult:
    """验证结果

    Attributes:
        decision: 验证决策
        reason: 决策原因（简短，用于日志/审计）
        caveats: 给用户的提示条目（NEEDS_REVISION 时由 replier 注入回复）
        confidence: 验证置信度 0.0-1.0（LLM 自评）
    """
    decision: VerifyDecision
    reason: str = ""
    caveats: list[str] = field(default_factory=list)
    confidence: float = 1.0


@runtime_checkable
class IVerifier(Protocol):
    """Agent 结果验证器接口

    实现方通过 DI（set_verifier）注入到 AgentService，graph.py 在执行循环
    结束后、生成最终回复前调用 verify()。未注入时跳过验证（向后兼容）。
    """

    async def verify(self, state: object) -> VerifyResult:
        """验证 Agent 执行结果

        Args:
            state: AgentState（避免循环导入用 object，实现方自行断言）

        Returns:
            VerifyResult — graph.py 据此决定后续流程
        """
        ...
