"""工具系统(D):工具规格。

M1 沿用 Agent 中的 schema 与校验逻辑;M2a 抽出 pydantic 参数模型 → JSON Schema,
并统一负责参数校验与错误反馈。
让工具的参数定义"只有一处",模型看到的 schema 和运行时校验的规则不会漂移。
"""
