
import re
import time
from pathlib import Path

import docker
from docker.errors import DockerException


class DockerTestRunner:

    def __init__(
        self,
        image="asea-sandbox:py312",
        timeout=120,
        memory_limit="512m"
    ):
        self.client = docker.from_env()
        self.image = image
        self.timeout = timeout
        self.memory_limit = memory_limit

    def parse_results(self, output, exit_code):
        counts = {
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
            "xfailed": 0,
            "xpassed": 0
        }

        for key in counts:
            match = re.search(
                rf"(\d+)\s+{key}",
                output,
                re.IGNORECASE
            )

            if match:
                counts[key] = int(match.group(1))

        failed_tests = re.findall(
            r"^FAILED\s+(.+?)(?:\s+-|$)",
            output,
            re.MULTILINE
        )

        total = sum(counts.values())

        if exit_code == 5:
            status = "NO_TESTS"
        elif exit_code == 0:
            status = "PASSED"
        else:
            status = "FAILED"

        return {
            "status": status,
            "total": total,
            **counts,
            "failed_tests": failed_tests
        }

    def run_tests(self, repository_path):

        repo_path = Path(repository_path).resolve()

        if not repo_path.is_dir():
            raise NotADirectoryError(repo_path)

        container = None
        start_time = time.monotonic()

        try:
            container = self.client.containers.create(
                image=self.image,
                command=[
                    "python", "-m", "pytest",
                    "-q", "-p", "no:cacheprovider", "-rA"
                ],
                working_dir="/workspace",
                volumes={
                    str(repo_path): {
                        "bind": "/workspace",
                        "mode": "ro"
                    }
                },
                network_disabled=True,
                read_only=True,
                user="65532:65532",
                mem_limit=self.memory_limit,
                nano_cpus=1_000_000_000,
                pids_limit=128,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                tmpfs={
                    "/tmp": (
                        "rw,noexec,nosuid,size=64m,"
                        "uid=65532,gid=65532"
                    )
                },
                environment={
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTEST_ADDOPTS": "",
                    "HOME": "/tmp",
                    "TMPDIR": "/tmp"
                },
                detach=True
            )

            container.start()

            timed_out = False

            try:
                # Returns the moment the container exits (no polling lag).
                container.wait(timeout=self.timeout)
            except Exception:  # noqa: BLE001 - timeout / connection error
                timed_out = True
                container.kill()

            output = container.logs(
                stdout=True,
                stderr=True
            ).decode("utf-8", errors="replace")

            duration = round(
                time.monotonic() - start_time, 2
            )

            if timed_out:
                return {
                    "status": "TIMEOUT",
                    "success": False,
                    "timed_out": True,
                    "duration": duration,
                    "output": output
                }

            exit_code = container.attrs["State"]["ExitCode"]

            results = self.parse_results(
                output,
                exit_code
            )

            return {
                **results,
                "success": exit_code == 0,
                "timed_out": False,
                "exit_code": exit_code,
                "duration": duration,
                "output": output
            }

        except DockerException as error:
            raise RuntimeError(
                f"Docker execution failed: {error}"
            ) from error

        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except DockerException:
                    pass
