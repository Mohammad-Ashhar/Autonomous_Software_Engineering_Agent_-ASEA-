import logging

from langgraph.graph import END, START, StateGraph

from asea.agents.state import AgentState
from asea.analysis.repository import inspect_repository
from asea.analysis.parser import parse_repository


logger = logging.getLogger(__name__)


def planner(state: AgentState) -> AgentState:
    """
    Planner/Supervisor Agent.

    Determines the next workflow step.
    """

    logger.info("Planner agent started")

    if state.get("error"):

        logger.error(
            "Workflow error: %s",
            state["error"]
        )

        return {
            "status": "escalate"
        }

    if not state.get("repository_path"):

        return {
            "status": "waiting_for_repository"
        }

    return {
        "status": "repository_analysis"
    }


def code_reader(state: AgentState) -> AgentState:
    """
    Code Reader Agent.

    Inspects repository structure and parses Python files.
    """

    logger.info("Code Reader Agent started")

    repository_path = state.get(
        "repository_path"
    )

    if not repository_path:

        return {
            "status": "failed",
            "error": "Repository path is missing"
        }

    try:

        inventory = inspect_repository(
            repository_path
        )

        parsed_files = parse_repository(
            repository_path,
            inventory["python_files"]
        )

        logger.info(
            "Successfully parsed %s Python files",
            len(parsed_files)
        )

        return {
            "inventory": inventory,
            "parsed_files": parsed_files,
            "status": "analysis_completed",
        }

    except Exception as error:

        logger.exception(
            "Code Reader Agent failed"
        )

        return {
            "status": "failed",
            "error": str(error)
        }


def route_after_planner(
    state: AgentState
) -> str:
    """Determine the next workflow node."""

    if state.get("status") == "repository_analysis":
        return "code_reader"

    return END


def build_workflow():
    """
    Construct and compile the ASEA LangGraph workflow.

    Current workflow:
        START -> Planner -> Code Reader -> END
    """

    workflow = StateGraph(AgentState)

    workflow.add_node(
        "planner",
        planner
    )

    workflow.add_node(
        "code_reader",
        code_reader
    )

    workflow.add_edge(
        START,
        "planner"
    )

    workflow.add_conditional_edges(
        "planner",
        route_after_planner,
        {
            "code_reader": "code_reader",
            END: END,
        }
    )

    workflow.add_edge(
        "code_reader",
        END
    )

    return workflow.compile()
