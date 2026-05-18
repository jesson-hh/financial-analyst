from __future__ import annotations
from typing import List
from pydantic import BaseModel


class AskOutput(BaseModel):
    answer: str
    actions_taken: List[str] = []      # e.g. ["search_memory(query='游资')", "read_past_report(code='SH600519')"]
    references: List[str] = []         # files/snippets cited

    needs_full_report: bool = False    # if true, CLI offers to dispatch full DAG
    suggested_code: str = ""           # code to use if needs_full_report
