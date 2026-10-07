"""Internal trusted construction for graph-bound inherited report research."""

from __future__ import annotations

from ..utils.safe_paths import validate_resource_id
from .knowledge_report_tools import KnowledgeReportTools, NeutralCapabilityError
from .report_agent import ReportAgent


def create_knowledge_report_agent(facade, graph_id: str, simulation_id: str,
                                  simulation_requirement: str, *, model_client,
                                  search_selector=None, interview_capability=None):
    """Bind one accepted projection and injected capabilities to the real agent."""
    if graph_id != facade._settings.display_graph_id:
        raise NeutralCapabilityError("graph_mismatch")
    if facade._settings.scope.get("layer") != "source":
        raise NeutralCapabilityError("unsupported_scope")
    try:
        validate_resource_id(simulation_id)
    except Exception:
        raise NeutralCapabilityError("simulation_mismatch") from None
    if (type(simulation_requirement) is not str
            or not 1 <= len(simulation_requirement.strip()) <= 4000
            or model_client is None or not callable(getattr(model_client, "chat", None))
            or not callable(getattr(model_client, "chat_json", None))
            or (search_selector is not None and not callable(search_selector))
            or (interview_capability is not None and not callable(interview_capability))):
        raise NeutralCapabilityError("invalid_request")
    graph = facade.graph_data(graph_id)
    tools = KnowledgeReportTools(
        graph_id=graph_id, simulation_id=simulation_id, graph=graph,
        scope=facade._settings.scope, llm_client=model_client,
        search_selector=search_selector, interview_capability=interview_capability)
    return ReportAgent(graph_id, simulation_id, simulation_requirement,
                       llm_client=model_client, zep_tools=tools, neutral_mode=True)


def create_connected_report_agent(context, *, requirement, output_language, model_client):
    """Child-only construction; every dependency is frozen or explicitly injected."""
    from .connected_report_context import validate_context
    from .connected_report_tools import lexical_selector
    context = validate_context(context)
    binding = context['binding']
    tools = KnowledgeReportTools(graph_id=binding['display_graph_id'],
        simulation_id=binding['preparation']['simulation_id'], graph=context['graph'], scope=binding['scope'],
        llm_client=model_client, search_selector=lexical_selector(context), interview_capability=None)
    return ReportAgent(binding['display_graph_id'], binding['preparation']['simulation_id'], requirement,
        llm_client=model_client, zep_tools=tools, neutral_mode=True,
        connected_context=context, output_language=output_language)
