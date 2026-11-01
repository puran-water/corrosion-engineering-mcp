"""PHREEQC runtime discovery, database resolution, and loadability probes."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import platform
import sys
from typing import Any, Iterable
DEFAULT_PHREEQC_DATABASE = 'phreeqc.dat'
DEFAULT_STATUS_DATABASES = ('phreeqc.dat', 'pitzer.dat', 'vitens.dat', 'Amm.dat', 'wateq4f_PWN.dat')

class PHREEQCBackendUnavailableError(RuntimeError):
    """Raised when phreeqpython/VIPhreeqc or a requested database cannot load."""

    def __init__(self, database: str, reason: str, *, database_path: str | None=None, viphreeqc_lib_path: str | None=None) -> None:
        self.database = database
        self.reason = reason
        self.database_path = database_path
        self.viphreeqc_lib_path = viphreeqc_lib_path
        super().__init__(f"PHREEQC backend unavailable for database '{database}': {reason}")

@dataclass(frozen=True)
class PhreeqcDatabaseProbe:
    """Loadability status for one PHREEQC database."""
    name: str
    ok: bool
    error: str | None = None

@dataclass(frozen=True)
class PhreeqcEngineStatus:
    """Corrosion-side PHREEQC engine status payload."""
    ready: bool
    viphreeqc_lib_path: str | None
    viphreeqc_arch: str | None
    databases_loadable: tuple[PhreeqcDatabaseProbe, ...]
    error: str | None = None

def phreeqpython_package_dir() -> Path:
    """Return the installed phreeqpython package directory."""
    try:
        import phreeqpython
    except Exception as exc:
        raise PHREEQCBackendUnavailableError(DEFAULT_PHREEQC_DATABASE, f'phreeqpython import failed: {exc}') from exc
    return Path(phreeqpython.__file__).resolve().parent

def viphreeqc_library_path() -> Path | None:
    """Return the platform-selected VIPhreeqc shared-library path."""
    try:
        base_dir = phreeqpython_package_dir()
    except PHREEQCBackendUnavailableError:
        return None
    if sys.platform == 'win32':
        lib_name = 'VIPhreeqc.dll'
    elif 'linux' in sys.platform:
        lib_name = 'viphreeqc.so'
    elif sys.platform == 'darwin':
        lib_name = 'viphreeqc.dylib'
    else:
        return None
    return base_dir / 'lib' / lib_name

def detect_shared_library_arch(path: str | Path) -> str | None:
    """Detect common ELF architectures without executing the shared library."""
    lib_path = Path(path)
    try:
        header = lib_path.read_bytes()[:64]
    except OSError:
        return None
    if len(header) < 20 or header[:4] != b'\x7fELF':
        return None
    endian_flag = header[5]
    if endian_flag == 1:
        endian = 'little'
    elif endian_flag == 2:
        endian = 'big'
    else:
        return None
    e_machine = int.from_bytes(header[18:20], endian)
    if e_machine == 62:
        return 'x86_64'
    if e_machine == 183:
        return 'aarch64'
    if e_machine == 3:
        return 'x86'
    if e_machine == 40:
        return 'armv7'
    return f'elf_machine_{e_machine}'

def _normalize_machine_arch(machine: str) -> str:
    normalized = machine.lower()
    if normalized == 'amd64':
        return 'x86_64'
    if normalized in {'arm64', 'armv8'}:
        return 'aarch64'
    if normalized.startswith('armv7') or normalized in {'armv7l', 'armv7hl'}:
        return 'armv7'
    if normalized.startswith('armv6') or normalized.startswith('armv5'):
        return 'arm'
    return normalized

def phreeqc_database_directory() -> Path:
    """Return the canonical bundled phreeqpython database directory."""
    return phreeqpython_package_dir() / 'database'
HOUSE_DATABASE_RULING_ID = 'PUBLIC-RULE-861b5d0d8643'

def repository_database_directory() -> Path:
    return Path(__file__).resolve().parents[1] / 'databases' / 'official'

def available_phreeqc_databases() -> tuple[str, ...]:
    """List the PHREEQC database files a bare name resolves to: the repository's and phreeqpython's bundled ones."""
    names = set()
    directories = [repository_database_directory()]
    try:
        directories.append(phreeqc_database_directory())
    except PHREEQCBackendUnavailableError:
        pass
    for database_dir in directories:
        if database_dir.exists():
            names.update((path.name for path in database_dir.glob('*.dat')))
    return tuple(sorted(names))
D5_RULING_ID = 'PUBLIC-RULE-762de2e5f9c5'

def resolve_phreeqc_database(database: str=DEFAULT_PHREEQC_DATABASE) -> tuple[str, Path, Path]:
    'Resolve a database name or file path to (name, directory, full_path).'
    database_path = Path(database).expanduser()
    if database_path.is_absolute():
        full_path = database_path.resolve()
        return (full_path.name, full_path.parent, full_path)
    if database_path.parent != Path('.'):
        raise PHREEQCBackendUnavailableError(str(database), f'a relative database path with a folder is never read against the working directory; name a bundled database or give an absolute path ({D5_RULING_ID})')
    house = repository_database_directory()
    if (house / database).is_file():
        return (database, house, house / database)
    database_dir = phreeqc_database_directory()
    full_path = database_dir / database
    return (database, database_dir, full_path)

def phreeqc_database_record(database: str=DEFAULT_PHREEQC_DATABASE) -> dict[str, str]:
    'Public engineering calculation interface.'
    import hashlib
    database_name, database_dir, database_path = resolve_phreeqc_database(database)
    try:
        bundled = database_dir.resolve() == phreeqc_database_directory().resolve()
    except PHREEQCBackendUnavailableError:
        bundled = False
    house = database_dir.resolve() == repository_database_directory()
    return {'database': database_name, 'source': 'repo-water-chemistry' if house else 'phreeqpython-bundled' if bundled else 'caller-path', 'sha256': hashlib.sha256(database_path.read_bytes()).hexdigest(), 'ruling': D5_RULING_ID}

def create_phreeqpython(database: str=DEFAULT_PHREEQC_DATABASE) -> Any:
    """Instantiate PhreeqPython after centralized database resolution."""
    database_name, database_dir, database_path = resolve_phreeqc_database(database)
    lib_path = viphreeqc_library_path()
    try:
        from phreeqpython import PhreeqPython
        return PhreeqPython(database=database_name, database_directory=database_dir)
    except Exception as exc:
        reason = str(exc) or exc.__class__.__name__
        if not database_path.exists():
            reason = f'database file not found: {database_name}'
        raise PHREEQCBackendUnavailableError(database_name, reason, database_path=str(database_path), viphreeqc_lib_path=str(lib_path) if lib_path else None) from exc

def _arch_warning(lib_path: Path | None, arch: str | None) -> str | None:
    machine = _normalize_machine_arch(platform.machine())
    if not lib_path:
        return 'VIPhreeqc shared library path could not be resolved'
    if not lib_path.exists():
        return f'VIPhreeqc shared library not found: {lib_path.name}'
    comparable = {'x86', 'x86_64', 'aarch64', 'armv7', 'arm'}
    if arch in comparable and machine in comparable and (arch != machine):
        return f'VIPhreeqc architecture mismatch: library is {arch}, host is {machine}'
    return None

def get_phreeqc_engine_status(databases: Iterable[str] | None=None) -> PhreeqcEngineStatus:
    """Probe actual PHREEQC loadability and bundled database loadability.

    ``ready`` means: the VIPhreeqc library is architecture-compatible AND the
    DEFAULT database loads. It deliberately does NOT require every optional
    database, because that produced a false negative with real consequences —
    ``corrosion_get_phreeqc_status`` reported ``ready=False`` on a host where
    PHREEQC demonstrably worked, purely because an older phreeqpython wheel
    shipped without ``Amm.dat``. On a server whose whole discipline is failing
    closed, a status that falsely says "unavailable" makes callers skip chemistry
    they could legitimately have run.

    Per-database results are still reported in ``databases_loadable``, so a caller
    that genuinely needs ``pitzer.dat`` for a brine can check that row rather than
    trusting a single aggregated boolean. An architecture mismatch still gates
    ``ready`` outright: in that case nothing loads at all.
    """
    lib_path = viphreeqc_library_path()
    lib_path_text = str(lib_path) if lib_path else None
    arch = detect_shared_library_arch(lib_path) if lib_path else None
    status_databases = tuple(databases or DEFAULT_STATUS_DATABASES)
    db_results: list[PhreeqcDatabaseProbe] = []
    arch_error = _arch_warning(lib_path, arch)
    first_error = arch_error
    for database in status_databases:
        try:
            create_phreeqpython(database)
            db_results.append(PhreeqcDatabaseProbe(name=Path(database).name, ok=True))
        except PHREEQCBackendUnavailableError as exc:
            if first_error is None:
                first_error = exc.reason
            db_results.append(PhreeqcDatabaseProbe(name=exc.database, ok=False, error=exc.reason))
    default_name = Path(status_databases[0]).name if status_databases else DEFAULT_PHREEQC_DATABASE
    default_status = next((item for item in db_results if item.name == default_name), None)
    ready = arch_error is None and bool(default_status and default_status.ok)
    return PhreeqcEngineStatus(ready=ready, viphreeqc_lib_path=lib_path_text, viphreeqc_arch=arch, databases_loadable=tuple(db_results), error=first_error)
__all__ = ['DEFAULT_PHREEQC_DATABASE', 'DEFAULT_STATUS_DATABASES', 'PHREEQCBackendUnavailableError', 'PhreeqcDatabaseProbe', 'PhreeqcEngineStatus', 'available_phreeqc_databases', 'create_phreeqpython', 'phreeqc_database_record', 'detect_shared_library_arch', 'get_phreeqc_engine_status', 'phreeqc_database_directory', 'repository_database_directory', 'HOUSE_DATABASE_RULING_ID', 'resolve_phreeqc_database', 'viphreeqc_library_path']
