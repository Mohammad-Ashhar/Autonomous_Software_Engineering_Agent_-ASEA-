
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from asea.core.graph import run_asea
from asea.integrations.repair_publisher import publish_validated_repairs
from asea.tools.analyzer import RepositoryAnalyzer
from asea.tools.repository import RepositoryManager

app = typer.Typer(
    name="asea",
    help="Autonomous Software Engineering Agent",
    no_args_is_help=True,
)

console = Console()


def resolve_repository(repository: str) -> Path:
    manager = RepositoryManager()

    if repository.startswith("https://"):
        console.print("[cyan]Cloning GitHub repository...[/cyan]")
        return manager.clone_repository(repository)

    return manager.load_local_repository(repository)


@app.command()
def analyze(
    repository: str = typer.Argument(
        ...,
        help="Local repository path or HTTPS GitHub repository URL.",
    ),
):
    """Analyse a repository and generate its Python code map."""

    try:
        repo_path = resolve_repository(repository)

        analyzer = RepositoryAnalyzer()

        result = analyzer.analyze_local_repository(repo_path)

        table = Table(title="ASEA Repository Analysis")

        table.add_column("Metric", style="cyan")
        table.add_column("Result", style="green")

        table.add_row("Repository", result["repository"])
        table.add_row("Path", result["path"])
        table.add_row("Python files", str(result["total_files"]))
        table.add_row("Functions", str(result["total_functions"]))
        table.add_row("Classes", str(result["total_classes"]))
        table.add_row("Imports", str(result["total_imports"]))
        table.add_row("Repository map", result["output_file"])

        console.print(table)
        console.print("[bold green]Analysis completed successfully![/bold green]")

    except Exception as error:  # noqa: BLE001
        console.print(f"[bold red]Analysis failed:[/bold red] {error}")
        raise typer.Exit(code=1)


@app.command()
def repair(
    repository: str = typer.Argument(
        ...,
        help="Local repository path or HTTPS GitHub repository URL.",
    ),
    publish: bool = typer.Option(
        False,
        "--publish",
        help="Publish validated and approved repairs through a GitHub PR.",
    ),
):
    """Detect bugs, generate patches, review and validate repairs."""

    try:
        repo_path = resolve_repository(repository)

        console.print("\n[bold cyan]Starting ASEA repair workflow...[/bold cyan]")
        console.print(f"Repository: {repo_path}\n")

        state = run_asea(str(repo_path))

        console.print("\n[bold]Workflow Summary[/bold]")
        console.print(f"Status: {state.get('status', 'UNKNOWN')}")
        console.print(f"Detected bugs: {len(state.get('detected_bugs', []))}")
        console.print(f"Repaired bugs: {len(state.get('repaired_bugs', []))}")
        console.print(f"Escalated bugs: {len(state.get('escalated_bugs', []))}")

        if state.get("working_test_results"):
            test_results = state["working_test_results"]
            console.print(
                f"Final tests: {test_results.get('passed', 0)} passed, "
                f"{test_results.get('failed', 0)} failed"
            )

        if publish:
            console.print("\n[bold cyan]Starting GitHub publishing...[/bold cyan]")

            result = publish_validated_repairs(state)

            console.print("\n[bold green]Publishing completed![/bold green]")
            console.print(f"Branch: {result.get('branch')}")
            console.print(f"Commit: {result.get('commit')}")

            pull_request = result.get("pull_request", {})
            console.print(f"Pull Request: {pull_request.get('url', 'Not available')}")

        elif state.get("status") == "COMPLETED":
            console.print(
                "\n[green]Repairs validated. Publishing was not requested.[/green]"
            )

        else:
            console.print(
                "\n[yellow]Workflow did not complete successfully. "
                "Publishing is unavailable.[/yellow]"
            )

    except Exception as error:  # noqa: BLE001
        console.print(f"\n[bold red]Repair workflow failed:[/bold red] {error}")
        raise typer.Exit(code=1)


@app.command()
def status():
    """Display ASEA project and environment information."""

    from asea.core.config import get_settings

    settings = get_settings()

    table = Table(title="ASEA Environment Status")

    table.add_column("Configuration", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("ASEA version", "0.1.0")
    table.add_row("Python version", "3.12+")
    table.add_row("LLM model", settings.ollama_model)
    table.add_row("Ollama endpoint", settings.ollama_base_url)
    table.add_row(
        "GitHub repository",
        settings.github_repository or "Not configured",
    )
    table.add_row(
        "GitHub token",
        "Configured" if settings.github_token else "Not configured",
    )

    console.print(table)


if __name__ == "__main__":
    app()
