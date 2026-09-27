from langgraph.graph import StateGraph, END
from utils.langgraph_state import TradingBrainState, fetch_screener_data, fetch_intelligence, prepare_llm_input

graph = StateGraph(TradingBrainState)

graph.add_node("fetch_screener", fetch_screener_data)
graph.add_node("fetch_intelligence", fetch_intelligence)
graph.add_node("prepare_input", prepare_llm_input)

graph.set_entry_point("fetch_screener")
graph.add_edge("fetch_screener", "fetch_intelligence")
graph.add_edge("fetch_intelligence", "prepare_input")
graph.add_edge("prepare_input", END)

def run_workflow(initial_state: TradingBrainState):
    return graph.compile().invoke(initial_state)
