# Stub — implementation pending (TDD RED phase)
# See .claude/tdd-handoff.md for the full implementation spec.

DEFAULT_CAP_GB: float = 0.0  # placeholder — must be >= 8 and <= 64 in real impl

# Win32 Job Object limit flag constants.
# CRITICAL: JOB_OBJECT_LIMIT_JOB_MEMORY must equal 0x00000200 (total-tree cap).
# JOB_OBJECT_LIMIT_PROCESS_MEMORY must equal 0x00000100 (per-process — weaker).
# These two must differ. Tests pin this explicitly to prevent regression to the
# reference harness's weaker per-process flag.
JOB_OBJECT_LIMIT_JOB_MEMORY: int = 0  # placeholder — wrong value; tests will fail
JOB_OBJECT_LIMIT_PROCESS_MEMORY: int = 0  # placeholder — wrong value; tests will fail

_CAP_INSTALLED: bool = False  # sentinel; True after successful install
_JOB_HANDLE = None  # Win32 HANDLE kept alive process-lifetime; None before install


def install_total_memory_cap(cap_bytes: int) -> None:
    """Stub — implementation pending (TDD RED phase)."""
    raise NotImplementedError("install_total_memory_cap not implemented")


def install_from_env() -> None:
    """Stub — implementation pending (TDD RED phase)."""
    raise NotImplementedError("install_from_env not implemented")
