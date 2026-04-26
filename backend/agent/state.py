from typing import TypedDict, List, Optional, Annotated
from langchain_core.messages import AnyMessage
import operator

class EggAgentState(TypedDict):
    lot_id: str
    image_normal_b64: Optional[str]        # base64 normal light image
    image_candling_b64: Optional[str]      # base64 candling image
    sensor_data: dict            # {weight_g, height_mm, diameter_mm}
    tool_results: dict           # accumulates all tool outputs
    messages: Annotated[List[AnyMessage], operator.add]   # LangChain messages (full conversation)
    iterations: int              # loop counter, max=8
    tool_call_counts: dict       # tracks {tool_name: count}
    final_grade: Optional[str]   # AA/A/B/C/Rejected
    confidence: Optional[float]
    needs_human_review: bool
    error_log: list
    
    vlm_pre_analysis: dict        # pre-computed vision data
    image_provided: bool          # flag

    # Grading (replaces LLM-decided grade)
    destination: Optional[str]          # "Commercial Sale"|"Hatchery"|"Industrial Processing"|"Industrial Waste"
    grading_reason: Optional[str]       # human-readable justification from egg_grader

    # Supply chain
    routing_decision: Optional[str]     # e.g. "Allocated to Carrefour — Order #3"
    stock_entry_id: Optional[int]       # DB id after inventory_allocator

    # Diagnostics
    diagnostic_alert: Optional[dict]    # from root_cause_analyzer, None if quality=good

    # Optional inputs (from /analyze request)
    lay_date: Optional[str]             # ISO date "YYYY-MM-DD"
    farm_zone: Optional[str]            # e.g. "Zone-Nord-B"
    quantity: Optional[int]             # number of eggs in this lot (default 1)
