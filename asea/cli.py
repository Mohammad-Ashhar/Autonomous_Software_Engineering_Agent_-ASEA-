import json
import logging

from pathlib import Path

import typer

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from asea.config import settings
from asea.logging_config import configure_logging

from asea.integrations.git import (
    clone_repository,
    validate_local_repository
)

from asea.analysis.repository import inspect_repository

from asea.analysis.parser import parse_repository

from asea.integrations.static_analysis import (
    run_ruff,
    run_semgrep
)

from asea.storage.database import RunStore


app = typer.Typer(
    name="asea",
    help="Autonomous Software Engineering Agent"
)

console = Console()

logger = logging.getLogger(__name__)


@app.command()
def analyze(
    source: str = typer.Argument(
        ...,
        help="Git repository URL or local repository path"
    ),

    local: bool = typer.Option(
        False,
        "--local",
        help="Analyze an existing local Git repository"
    ),

    workspace: Path = typer.Option(
        Path(".workspace"),
        "--workspace",
        "-w",
        help="Repository workspace directory"
    ),

    static_checks: bool = typer.Option(
        False,
        "--static-checks",
        help="Run Ruff and Semgrep"
    ),

    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v"
    )
):
    """
    Clone or inspect a repository and analyze its structure.
    """

    configure_logging(verbose)

    console.print(
        Panel(
            "[bold cyan]ASEA[/bold cyan]\n"
            "Autonomous Software Engineering Agent",
            title="Starting Analysis"
        )
    )

    try:

        # Step 1: Repository acquisition

        console.print(
            "[yellow]Step 1:[/yellow] Repository acquisition"
        )

        if local:

            repository_path = validate_local_repository(
                source
            )

        else:

            repository_path = clone_repository(
                source,
                workspace
            )

        console.print(
            f"[green]Repository:[/green] {repository_path}"
        )

        # Step 2: Repository inventory

        console.print(
            "[yellow]Step 2:[/yellow] Repository inspection"
        )

        inventory = inspect_repository(
            repository_path
        )

        # Step 3: Source-code parsing

        console.print(
            "[yellow]Step 3:[/yellow] Tree-sitter parsing"
        )

        parsed_files = parse_repository(
            repository_path,
            inventory["python_files"]
        )

        summary = {
            "inventory": inventory,
            "parsed_files": parsed_files,
        }

        # Step 4: Optional static analysis

        if static_checks:

            console.print(
                "[yellow]Step 4:[/yellow] Static analysis"
            )

            summary["ruff"] = run_ruff(
                repository_path
            )

            summary["semgrep"] = run_semgrep(
                repository_path
            )

        # Step 5: Save execution history

        store = RunStore(
            settings.DATABASE_PATH
        )

        run_id = store.save_run(
            str(repository_path),
            "completed",
            summary
        )

        # Step 6: Display results

        table = Table(
            title="ASEA Repository Analysis"
        )

        table.add_column("Metric")
        table.add_column("Result", justify="right")

        table.add_row(
            "Repository",
            str(repository_path)
        )

        table.add_row(
            "Total Files",
            str(inventory["file_count"])
        )

        table.add_row(
            "Directories",
            str(inventory["directory_count"])
        )

        table.add_row(
            "Python Files",
            str(inventory["python_file_count"])
        )

        declaration_count = sum(
            len(item.get("declarations", []))
            for item in parsed_files
        )

        table.add_row(
            "Functions and Classes",
            str(declaration_count)
        )

        table.add_row(
            "Execution ID",
            str(run_id)
        )

        console.print(table)

        # Step 7: Export analysis report

        output_directory = Path(".asea")

        output_directory.mkdir(
            parents=True,
            exist_ok=True
        )

        report_path = (
            output_directory /
            f"analysis-run-{run_id}.json"
        )

        report_path.write_text(
            json.dumps(
                summary,
                indent=4
            ),
            encoding="utf-8"
        )

        console.print(
            Panel(
                "[bold green]Analysis completed successfully![/bold green]\n"
                f"Report: {report_path}\n"
                "No repository source files were modified.",
                title="ASEA Results"
            )
        )

    except Exception as error:

        logger.exception(
            "ASEA analysis failed"
        )

        console.print(
            f"[bold red]Error:[/bold red] {error}"
        )

        raise typer.Exit(code=1)


@app.command()
def history(
    limit: int = typer.Option(
        10,
        min=1,
        max=100
    )
):
    """Display recent ASEA analysis executions."""

    store = RunStore(
        settings.DATABASE_PATH
    )

    runs = store.recent_runs(limit)

    if not runs:

        console.print(
            "No execution history found."
        )

        return

    table = Table(
        title="ASEA Execution History"
    )

    table.add_column("ID")
    table.add_column("Repository")
    table.add_column("Status")
    table.add_column("Started At")

    for run in runs:

        table.add_row(
            str(run["id"]),
            run["repository_path"],
            run["status"],
            run["started_at"]
        )

    console.print(table)


if __name__ == "__main__":
    app()
