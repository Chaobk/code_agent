import re
from enum import Enum

class Decision(Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"

DENY_PATTERNS = [
    r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+/",   # rm -rf / 及其变体
    r"\bmkfs\.",
    r"\bdd\s+.*of=/dev/",
    r"\bsudo\b",
    r"\bcurl\b.*\|\s*(ba)?sh",
]

ALLOW_COMMANDS = {"ls", "cat", "head", "tail", "grep", "wc", "pwd", "which", "git"}

def check_command(command: str) -> Decision:
    for p in DENY_PATTERNS:
        if re.search(p, command):
            return Decision.DENY

    # 用来分开复合命令
    segments = [s for s in re.split(r"\s*(?:&&|\|\||;|\|)\s*", command) if s.strip()]
    decision = [check_single(s) for s in segments]
    if Decision.DENY in decision:
        return Decision.DENY
    if Decision.ASK in decision:
        return Decision.ASK

    return Decision.ALLOW


def check_single(segment: str) -> Decision:
    # 如果涉及到了重定向，肯定是编辑类的命令，统一 ASK
    if re.search(r"(?<![0-9&])>(?!>)\s*(?!/dev/null)", segment):
        return Decision.ASK
    parts = segment.strip().split()
    if parts and parts[0] in ALLOW_COMMANDS:
        return Decision.ALLOW
    return Decision.ASK