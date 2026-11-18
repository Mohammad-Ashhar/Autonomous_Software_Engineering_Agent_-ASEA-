from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict


class GraphState(TypedDict):
    message: str
    decision: str


def approval_node(state: GraphState):
    response = interrupt(
        {
            "type": "patch_review",
            "message": state["message"],
        }
    )

    return {
        "decision": response["decision"],
    }


def build_test_graph(checkpointer):
    workflow = StateGraph(GraphState)

    workflow.add_node("approval", approval_node)

    workflow.add_edge(START, "approval")
    workflow.add_edge("approval", END)

    return workflow.compile(checkpointer=checkpointer)


def test_interrupt_and_resume():
    with SqliteSaver.from_conn_string(":memory:") as checkpointer:
        checkpointer.setup()

        graph = build_test_graph(checkpointer)

        config = {
            "configurable": {
                "thread_id": "asea-test-001",
            }
        }

        initial_state = {
            "message": "Approve this test patch?",
            "decision": "",
        }

        graph.invoke(
            initial_state,
            config=config,
        )

        snapshot = graph.get_state(config)

        assert snapshot.next == ("approval",)
        assert snapshot.tasks[0].interrupts

        resumed_result = graph.invoke(
            Command(
                resume={
                    "decision": "approved",
                }
            ),
            config=config,
        )

        assert resumed_result["decision"] == "approved"


def test_interrupt_checkpoint_survives_checkpointer_restart(tmp_path):
    database_path = tmp_path / "restart-checkpoint.db"
    config = {
        "configurable": {
            "thread_id": "asea-restart-review-001",
        }
    }
    initial_state = {
        "message": "Approve this patch after restart?",
        "decision": "",
    }

    with SqliteSaver.from_conn_string(str(database_path)) as checkpointer:
        checkpointer.setup()
        graph = build_test_graph(checkpointer)
        graph.invoke(initial_state, config=config)
        paused = graph.get_state(config)
        assert paused.interrupts

    with SqliteSaver.from_conn_string(str(database_path)) as checkpointer:
        checkpointer.setup()
        graph = build_test_graph(checkpointer)
        restored = graph.get_state(config)
        assert restored.interrupts
        assert restored.values["message"] == initial_state["message"]

        result = graph.invoke(
            Command(resume={"decision": "approved"}),
            config=config,
        )

    assert result["decision"] == "approved"
