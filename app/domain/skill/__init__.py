"""Skill domain - Skill 沉淀与自进化领域逻辑

模块：
  - recorder: 任务→Workflow→Skill 录制
  - extractor: LLM 从任务轨迹抽取 Skill 草稿
  - failure_analyzer: 失败任务分析 → 避坑 Skill 草稿
  - optimizer: Skill 自进化引擎（执行反馈→自动优化）
  - evolution: 经验固化器 + Review 系统（草稿/修订审批）
"""
