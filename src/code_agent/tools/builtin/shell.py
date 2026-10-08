"""内置工具(D/F):shell 执行。

M1 实现 ``BashArgs`` 与异步 ``run_bash``,向模型注册为 ``bash``:
固定执行目录,返回 stdout / stderr / 退出码 / 超时与截断信息。
具体参数、输出预算与进程清理约定见 PLAN.md 的 M1 规格。
M3 起它的调用要先过 ``safety`` 的策略检查。
"""
