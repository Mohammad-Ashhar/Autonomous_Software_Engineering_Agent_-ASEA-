"""Development server entry point that never watches repair workspaces."""

import os

import uvicorn


def run() -> None:
    """Run the API without a file watcher.

    ASEA intentionally edits files inside dynamically created repository
    workspaces. A reload watcher can mistake those edits for application
    source changes, terminate an active LangGraph run, and close its SQLite
    checkpointer while the run is still writing checkpoints.
    """
    uvicorn.run(
        "asea.api.main:app",
        host=os.getenv("ASEA_API_HOST", "127.0.0.1"),
        port=int(os.getenv("ASEA_API_PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    run()
