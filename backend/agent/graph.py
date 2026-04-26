# backend/agent/graph.py — Version finale corrigée
import os
import json
import logging
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage, AIMessage
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama
from backend.agent.state import EggAgentState
from backend.agent.prompts import SYSTEM_PROMPT

from backend.tools.vision_tools import (
    vlm_egg_analyzer, candling_analyzer, egg_detector, visual_egg_grader,
    _is_placeholder
)
from backend.tools.search_tools import web_search_tool, article_fetcher
from backend.tools.rag_tools import regulatory_rag_tool
from backend.tools.output_tools import alert_and_logger, report_and_qr_generator, invoice_generator
from backend.tools.supply_tools import (
    egg_grader, inventory_allocator, root_cause_analyzer,
    check_stock, allocate_lot, notify_shortage, grade_regulation_resolver
)
from backend.tools.db_query_tool import db_query_tool
from backend.tools.partner_discovery_tool import partner_discovery_tool

logger = logging.getLogger(__name__)

# ── Store global pour les images ─────────────────────────────
# LangGraph MemorySaver vide les grandes valeurs entre les nœuds.
# On stocke l'image ici pour la récupérer de façon fiable.
IMAGE_STORE: dict = {}  # {lot_id: {"normal": b64, "candling": b64}}

def store_image(lot_id: str, normal_b64: str = None, candling_b64: str = None):
    """Appeler AVANT graph.astream() pour stocker l'image."""
    IMAGE_STORE[lot_id] = {
        "normal":   normal_b64   or "",
        "candling": candling_b64 or ""
    }
    logger.info(f"[IMAGE_STORE] Stored image for {lot_id} — size={len(normal_b64 or '')} chars")

def get_image(lot_id: str) -> str:
    """Récupère l'image normale depuis le store."""
    return IMAGE_STORE.get(lot_id, {}).get("normal", "")

def cleanup_image(lot_id: str):
    """Libère la mémoire après analyse."""
    IMAGE_STORE.pop(lot_id, None)

# ── Tools ────────────────────────────────────────────────────
tools = [
    egg_detector,
    visual_egg_grader,
    vlm_egg_analyzer,
    grade_regulation_resolver,
    egg_grader,
    inventory_allocator,
    alert_and_logger,
    report_and_qr_generator,
    regulatory_rag_tool,
    web_search_tool,
    db_query_tool,
    partner_discovery_tool,
]


def get_llm():
    groq_llm = ChatGroq(
        model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        api_key=os.getenv("GROQ_API_KEY"),
        temperature=0
    )
    try:
        ollama_llm = ChatOllama(
            model="llama3.1:8b",
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            temperature=0
        )
        return groq_llm.bind_tools(tools).with_fallbacks([ollama_llm.bind_tools(tools)])
    except Exception:
        return groq_llm.bind_tools(tools)


llm = get_llm()


async def agent_node(state: EggAgentState) -> dict:
    iters     = state.get("iterations", 0)
    MAX_ITERS = int(os.getenv("MAX_AGENT_ITERATIONS", "12"))
    if iters >= MAX_ITERS:
        return {"messages": state["messages"]}
    try:
        response = await llm.ainvoke(state["messages"])
        return {"messages": [response], "iterations": iters + 1}
    except Exception as e:
        logger.error(f"[agent_node] LLM error: {e}")
        return {"messages": [AIMessage(content=f"Agent error: {str(e)}")], "iterations": iters + 1}


def _deserialize_args(tool_name: str, args: dict) -> dict:
    DICT_FIELDS = {
        "egg_grader":              ["cnn_result", "regulation", "vlm_result"],
        "inventory_allocator":     ["grader_result"],
        "alert_and_logger":        ["quality_result", "fertility_result", "vlm_result"],
        "report_and_qr_generator": ["grader_result"],
        "partner_discovery_tool":  [],
    }
    clean = dict(args)
    for field in DICT_FIELDS.get(tool_name, []):
        val = clean.get(field)
        if isinstance(val, str):
            try:
                clean[field] = json.loads(val)
            except Exception:
                clean[field] = {}
        elif val is None:
            clean[field] = {}
    return clean


async def tool_node(state: EggAgentState) -> dict:
    last_message     = state["messages"][-1]
    tool_map         = {t.name: t for t in tools}
    tool_outputs     = []
    tool_call_counts = dict(state.get("tool_call_counts") or {})
    tool_results     = dict(state.get("tool_results") or {})
    if "crops" not in tool_results:
        tool_results["crops"] = {}

    # ── Récupérer l'image depuis IMAGE_STORE (fiable) ─────
    lot_id_state = state.get("lot_id", "")
    real_image   = get_image(lot_id_state)

    # Fallback : essayer depuis l'état LangGraph
    if not real_image or len(real_image) < 500:
        real_image = state.get("image_normal_b64") or state.get("image_candling_b64") or ""

    logger.info(f"[tool_node] lot={lot_id_state} | image_size={len(real_image)} | tools={[tc['name'] for tc in last_message.tool_calls]}")

    for tool_call in last_message.tool_calls:
        tool_name    = tool_call["name"]
        args_to_pass = dict(tool_call["args"])

        logger.info(f"[tool_node] ── {tool_name}")

        # ── Limite 2 appels par outil ─────────────────────
        count = tool_call_counts.get(tool_name, 0)
        if count >= 2:
            tool_outputs.append(ToolMessage(
                content=json.dumps({"error": f"{tool_name} already called {count}x", "fallback": True}),
                name=tool_name, tool_call_id=tool_call["id"]
            ))
            continue
        tool_call_counts[tool_name] = count + 1

        # ══════════════════════════════════════════════════
        # INJECTIONS PAR OUTIL
        # ══════════════════════════════════════════════════

        # ── egg_detector ───────────────────────────────────
        if tool_name == "egg_detector":
            img_arg = args_to_pass.get("image_input", "")
            if _is_placeholder(img_arg) or img_arg == "use_state_image":
                # Chercher dans IMAGE_STORE
                lot_id_key = state.get("lot_id", "")
                img_from_store = IMAGE_STORE.get(lot_id_key, {}).get("normal", "")
                if img_from_store and len(img_from_store) > 500:
                    args_to_pass["image_input"] = img_from_store
                    logger.info(f"[tool_node] egg_detector: injected from store for {lot_id_key}")
                elif real_image and len(real_image) > 500:
                    args_to_pass["image_input"] = real_image
                    logger.info(f"[tool_node] egg_detector: injected from fallback real_image")
                else:
                    logger.warning(f"[tool_node] egg_detector: NO IMAGE AVAILABLE")

        # ── visual_egg_grader ──────────────────────────────
        elif tool_name == "visual_egg_grader":
            crop_arg = args_to_pass.get("crop_b64", "")
            if isinstance(crop_arg, str) and crop_arg.startswith("<crop_for_"):
                egg_id = crop_arg.replace("<crop_for_", "").replace(">", "")
                if egg_id in tool_results.get("crops", {}):
                    args_to_pass["crop_b64"] = tool_results["crops"][egg_id]
                    logger.info(f"[tool_node] visual_egg_grader: resolved crop for {egg_id}")
                elif tool_results.get("crops"):
                    args_to_pass["crop_b64"] = list(tool_results["crops"].values())[0]
                else:
                    args_to_pass["crop_b64"] = real_image
            elif _is_placeholder(crop_arg):
                if tool_results.get("crops"):
                    args_to_pass["crop_b64"] = list(tool_results["crops"].values())[0]
                    logger.info(f"[tool_node] visual_egg_grader: using first stored crop")
                else:
                    args_to_pass["crop_b64"] = real_image
                    logger.info(f"[tool_node] visual_egg_grader: using real_image as crop ({len(real_image)} chars)")

        # ── vlm_egg_analyzer ───────────────────────────────
        elif tool_name == "vlm_egg_analyzer":
            if not args_to_pass.get("vlm_data"):
                pre = state.get("vlm_pre_analysis", {})
                if pre and len(pre) > 2:
                    args_to_pass["vlm_data"] = pre
            if not args_to_pass.get("image_normal_b64") or _is_placeholder(args_to_pass.get("image_normal_b64", "")):
                args_to_pass["image_normal_b64"] = real_image
            if not args_to_pass.get("lot_id"):
                args_to_pass["lot_id"] = lot_id_state

        # ── Désérialisation ────────────────────────────────
        args_to_pass = _deserialize_args(tool_name, args_to_pass)

        # ── egg_grader ─────────────────────────────────────
        if tool_name == "egg_grader":
            if not args_to_pass.get("cnn_result"):
                args_to_pass["cnn_result"]  = tool_results.get("visual_egg_grader", {})
            if not args_to_pass.get("vlm_result"):
                args_to_pass["vlm_result"]  = tool_results.get("vlm_egg_analyzer", {})
            if not args_to_pass.get("regulation"):
                args_to_pass["regulation"]  = tool_results.get("grade_regulation_resolver", {})
            if not args_to_pass.get("egg_id"):
                args_to_pass["egg_id"] = f"egg_0_{lot_id_state}"

        # ── inventory_allocator ────────────────────────────
        elif tool_name == "inventory_allocator":
            grader = tool_results.get("egg_grader", {})
            if not args_to_pass.get("lot_id"):
                args_to_pass["lot_id"]      = lot_id_state
            if not args_to_pass.get("grade"):
                args_to_pass["grade"]       = grader.get("final_grade", "E")
            if not args_to_pass.get("destination"):
                args_to_pass["destination"] = grader.get("destination", "")
            if not args_to_pass.get("size_class"):
                args_to_pass["size_class"]  = "M"

        # ── alert_and_logger ───────────────────────────────
        elif tool_name == "alert_and_logger":
            grader = tool_results.get("egg_grader", {})
            vlm    = tool_results.get("vlm_egg_analyzer", {})
            args_to_pass.setdefault("lot_id",              lot_id_state)
            args_to_pass.setdefault("grade",               grader.get("final_grade", "E"))
            args_to_pass.setdefault("destination",         grader.get("destination", "unknown"))
            args_to_pass.setdefault("confidence",          grader.get("confidence", 0.0))
            args_to_pass.setdefault("reasoning",           grader.get("grading_source", "unknown"))
            args_to_pass.setdefault("blood_spot_detected", vlm.get("blood_spot_detected", False))
            args_to_pass.setdefault("crack_detected",      vlm.get("crack_detected", False))
            args_to_pass.setdefault("crack_severity",      vlm.get("crack_severity", "none"))
            args_to_pass.setdefault("needs_human_review",  grader.get("needs_human_review", False))

        # ── report_and_qr_generator ────────────────────────
        elif tool_name == "report_and_qr_generator":
            grader = tool_results.get("egg_grader", {})
            args_to_pass.setdefault("lot_id",           lot_id_state)
            args_to_pass.setdefault("grade",            grader.get("final_grade"))
            args_to_pass.setdefault("destination",      grader.get("destination"))
            args_to_pass.setdefault("grader_result",    grader)
            args_to_pass.setdefault("market_price_tnd", grader.get("market_price_TND"))

        # ── lot_id universel ──────────────────────────────
        if tool_name in ("inventory_allocator", "alert_and_logger", "report_and_qr_generator"):
            if not args_to_pass.get("lot_id"):
                args_to_pass["lot_id"] = lot_id_state

        # ── partner_discovery_tool ────────────────────────
        elif tool_name == "partner_discovery_tool":
            grader = tool_results.get("egg_grader", {})
            args_to_pass.setdefault("grade", grader.get("final_grade", "A"))
            args_to_pass.setdefault("size", "M")
            args_to_pass.setdefault("quantity", 1) # Par défaut 1 œuf par lot analysé
            args_to_pass.setdefault("price_tnd", grader.get("market_price_TND", 0.40))

        # ══════════════════════════════════════════════════
        # EXÉCUTION
        # ══════════════════════════════════════════════════
        tool_res = ""
        try:
            if tool_name not in tool_map:
                tool_res = json.dumps({"error": f"Tool '{tool_name}' not registered", "fallback": True})
            else:
                res = await tool_map[tool_name].ainvoke(args_to_pass)

                # TPM Shield : stocker crops et remplacer par placeholder
                if tool_name == "egg_detector" and isinstance(res, dict):
                    for egg in res.get("eggs", []):
                        if "cropped_image" in egg:
                            eid = egg.get("egg_id", "unknown")
                            tool_results["crops"][eid] = egg["cropped_image"]
                            egg["cropped_image"] = f"<crop_for_{eid}>"
                            logger.info(f"[tool_node] Stored crop {eid} ({len(tool_results['crops'][eid])} chars)")

                tool_res = json.dumps(res)

                # Stocker résultat pour tools suivants
                if isinstance(res, dict):
                    tool_results[tool_name] = res

        except Exception as e:
            logger.error(f"[tool_node] {tool_name} EXCEPTION: {e}", exc_info=True)
            tool_res = json.dumps({"error": str(e), "fallback": True, "tool": tool_name})

        tool_outputs.append(ToolMessage(
            content=tool_res,
            name=tool_name,
            tool_call_id=tool_call["id"]
        ))

    return {
        "messages":         tool_outputs,
        "tool_call_counts": tool_call_counts,
        "tool_results":     tool_results,
    }


def should_continue(state: EggAgentState) -> str:
    last = state["messages"][-1]
    if not hasattr(last, "tool_calls") or not last.tool_calls:
        return END
    if state.get("iterations", 0) >= int(os.getenv("MAX_AGENT_ITERATIONS", "12")):
        return END
    return "tools"


def create_egg_agent_graph():
    graph = StateGraph(EggAgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue, ["tools", END])
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=MemorySaver())
