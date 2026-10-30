"""JSONL 轨迹记录与只读回放（待实现）。"""

import json
from typing import TextIO


class TraceWriter:
    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def write_record(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False)
        self._stream.write(line + "\n")
        self._stream.flush()
