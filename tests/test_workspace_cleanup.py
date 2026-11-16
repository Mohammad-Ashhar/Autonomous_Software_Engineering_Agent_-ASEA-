from asea.api.workspace_cleanup import cleanup_repair_workspace


def test_cleanup_removes_only_the_job_workspace(tmp_path):
    repository = tmp_path / "source"
    repository.mkdir()
    run_directory = tmp_path / "workspaces" / "asea_run_test"
    working_repo = run_directory / "repository"
    working_repo.mkdir(parents=True)
    (working_repo / "source.py").write_text("print('ok')", encoding="utf-8")

    assert cleanup_repair_workspace(
        repository,
        {"working_repo_path": str(working_repo)},
    )
    assert not run_directory.exists()
    assert repository.is_dir()


def test_cleanup_rejects_paths_outside_workspace(tmp_path):
    repository = tmp_path / "source"
    repository.mkdir()
    external = tmp_path / "important"
    external.mkdir()

    assert not cleanup_repair_workspace(
        repository,
        {"working_repo_path": str(external)},
    )
    assert external.is_dir()


def test_cleanup_ignores_missing_workspace_state(tmp_path):
    repository = tmp_path / "source"
    repository.mkdir()

    assert not cleanup_repair_workspace(repository, {})


def test_cleanup_rejects_symlinked_run_directory(tmp_path):
    repository = tmp_path / "source"
    repository.mkdir()
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    external_run = tmp_path / "external_run"
    external_repo = external_run / "repository"
    external_repo.mkdir(parents=True)
    (external_repo / "keep.txt").write_text("keep", encoding="utf-8")
    linked_run = workspace_root / "asea_run_link"
    linked_run.symlink_to(external_run, target_is_directory=True)

    assert not cleanup_repair_workspace(
        repository,
        {"working_repo_path": str(linked_run / "repository")},
    )
    assert (external_repo / "keep.txt").is_file()


def test_cleanup_rejects_symlinked_repository_directory(tmp_path):
    repository = tmp_path / "source"
    repository.mkdir()
    run_directory = tmp_path / "workspaces" / "asea_run_link"
    run_directory.mkdir(parents=True)
    external_repo = tmp_path / "external_repo"
    external_repo.mkdir()
    (external_repo / "keep.txt").write_text("keep", encoding="utf-8")
    linked_repo = run_directory / "repository"
    linked_repo.symlink_to(external_repo, target_is_directory=True)

    assert not cleanup_repair_workspace(
        repository,
        {"working_repo_path": str(linked_repo)},
    )
    assert (external_repo / "keep.txt").is_file()



def test_cleanup_of_one_job_preserves_another_jobs_workspace(tmp_path):
    repository = tmp_path / "source"
    repository.mkdir()
    workspace_root = tmp_path / "workspaces"

    first_workspace = workspace_root / "asea_run_first" / "repository"
    second_workspace = workspace_root / "asea_run_second" / "repository"
    first_workspace.mkdir(parents=True)
    second_workspace.mkdir(parents=True)
    (first_workspace / "candidate.py").write_text("first", encoding="utf-8")
    (second_workspace / "candidate.py").write_text("second", encoding="utf-8")

    assert cleanup_repair_workspace(
        repository,
        {"working_repo_path": str(first_workspace)},
    )

    assert not first_workspace.parent.exists()
    assert (second_workspace / "candidate.py").read_text(encoding="utf-8") == "second"



def test_managed_clone_uses_existing_workspace_root(tmp_path, monkeypatch):
    from asea.api import workspace_cleanup

    managed_root = tmp_path / "workspaces"
    managed_root.mkdir()
    monkeypatch.chdir(tmp_path)
    repository = managed_root / "owner__repo"
    repository.mkdir()
    run_directory = managed_root / "asea_run_managed"
    working_repo = run_directory / "repository"
    working_repo.mkdir(parents=True)
    (working_repo / "module.py").write_text("value = 1", encoding="utf-8")

    assert workspace_cleanup.repair_workspace_root(repository) == managed_root
    assert workspace_cleanup.cleanup_repair_workspace(
        repository,
        {"working_repo_path": str(working_repo)},
    )
    assert not run_directory.exists()
    assert repository.is_dir()
