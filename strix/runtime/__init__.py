import os

from .runtime import AbstractRuntime


def get_runtime() -> AbstractRuntime:
    runtime_backend = os.getenv("STRIX_RUNTIME_BACKEND", "docker")

    if runtime_backend == "docker":
        from .docker_runtime import DockerRuntime

        return DockerRuntime()

    if runtime_backend == "local":
        from .local_runtime import LocalRuntime

        return LocalRuntime()

    raise ValueError(
        f"Unsupported runtime backend: {runtime_backend}. Only 'docker' and 'local' are supported for now."
    )


__all__ = ["AbstractRuntime", "get_runtime"]
