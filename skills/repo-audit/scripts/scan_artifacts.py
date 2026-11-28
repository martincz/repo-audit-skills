#!/usr/bin/env python3
"""Bounded read-only filesystem metadata probe for pipeline efficiency audits.

Pure Python 3 standard library (Python 3.9+, zero third-party dependencies, zero file
writes, zero file-content reads, non-interactive). Safe under isolated execution:

    python3 -I -S -B skills/repo-audit/scripts/scan_artifacts.py --root . --json

Reports objective observed metadata only (never enforces pass/fail thresholds, reads
file contents, or runs AST checks; `ok: true` indicates probe execution succeeded).
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import errno
import json
import math
import os
import stat
import sys
import time
from typing import Any

SCHEMA_VERSION = "1.0"
DEFAULT_MAX_FILES = 200_000
DEFAULT_MAX_DEPTH = 12
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_TOP_N = 15
MAX_GAP_SAMPLES = 20

DEFAULT_EXCLUDE_DIRS: tuple[str, ...] = (
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
)
DEFAULT_SCRIPT_DIR_NAMES = frozenset({"scripts", "tools", "bin", "jobs", "pipelines"})
ARCHIVE_SUBDIR_NAMES = frozenset({"archive", "legacy", "deprecated"})
TRUNCATION_REASON_ORDER: tuple[str, ...] = ("max_files", "max_depth", "timeout")

SIZE_BUCKET_SPECS: tuple[tuple[str, int | None], ...] = (
    ("<1KiB", 1024),
    ("1KiB-10KiB", 10 * 1024),
    ("10KiB-100KiB", 100 * 1024),
    ("100KiB-1MiB", 1024 * 1024),
    ("1MiB-50MiB", 50 * 1024 * 1024),
    (">=50MiB", None),
)

COMPOUND_EXTENSIONS = frozenset({
    ".json.gz", ".jsonl.gz", ".ndjson.gz", ".csv.gz", ".tsv.gz",
    ".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst",
    ".log.gz", ".txt.gz", ".xml.gz",
    ".json.zst", ".jsonl.zst", ".ndjson.zst", ".csv.zst", ".tsv.zst",
    ".json.bz2", ".csv.bz2", ".json.xz", ".csv.xz",
})

COMMON_CODE_EXTENSIONS = frozenset({
    ".py", ".pyi", ".sh", ".bash", ".zsh", ".fish", ".ksh", ".ps1", ".bat", ".cmd",
    ".js", ".mjs", ".cjs", ".ts", ".mts", ".cts", ".jsx", ".tsx",
    ".rb", ".pl", ".pm", ".php", ".lua", ".awk", ".sed",
    ".go", ".rs", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh",
    ".java", ".kt", ".kts", ".scala", ".groovy", ".cs", ".fs",
    ".r", ".jl", ".m", ".swift", ".zig", ".nim", ".ex", ".exs", ".sql", ".mk",
})

HAS_ST_BLOCKS = hasattr(os.stat_result, "st_blocks")
SUPPORTS_FD_SCANDIR = (
    os.scandir in os.supports_fd
    and os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
)


class CLIUsageError(ValueError):
    """Raised on invalid CLI arguments or disallowed target paths (exit code 2)."""


class CapabilityError(RuntimeError):
    """Raised when the host platform lacks safe dir_fd + O_NOFOLLOW traversal (exit code 3)."""


@dataclasses.dataclass
class RootSpec:
    kind: str  # "repo_root" | "artifact_dir"
    input_path: str
    resolved_path: str
    device_id: int
    dir_key: tuple[int, int] | None
    repo_rel_prefix: str | None = None
    entries_visited: int = 0
    directories_scanned: int = 0
    logical_files: int = 0
    logical_bytes: int = 0
    unique_files: int = 0
    unique_bytes: int = 0
    known_inode_logical_files: int = 0
    unknown_inode_files: int = 0


@dataclasses.dataclass
class DirAccum:
    root_path: str
    rel_path: str
    parent_idx: int | None
    depth: int
    direct_entries: int = 0
    direct_logical_files: int = 0
    direct_logical_bytes: int = 0
    direct_subdirs: int = 0
    total_paths: int = 0
    total_logical_files: int = 0
    total_logical_bytes: int = 0


@dataclasses.dataclass
class ExtAccum:
    logical_files: int = 0
    logical_bytes: int = 0


@dataclasses.dataclass
class ScriptDirAccum:
    path: str
    source: str
    logical_files: int = 0
    logical_bytes: int = 0
    code_extensions: dict[str, int] = dataclasses.field(default_factory=lambda: collections.defaultdict(int))
    non_code_files: int = 0
    archive_logical_files: int = 0
    archive_subdirs: dict[str, dict[str, Any]] = dataclasses.field(default_factory=dict)


class GapTracker:
    """Tracks bounded coverage gaps and OS errors encountered during probing."""

    def __init__(self, sample_limit: int = MAX_GAP_SAMPLES) -> None:
        self.sample_limit = sample_limit
        self.permission_errors = 0
        self.file_not_found_errors = 0
        self.other_os_errors = 0
        self.symlinks_skipped = 0
        self.cross_device_skipped = 0
        self.max_depth_skipped = 0
        self.special_files_skipped = 0
        self.samples: list[dict[str, str]] = []
        self.samples_truncated = False
        self._seen_error_keys: set[tuple[str, str]] = set()

    @property
    def total_errors(self) -> int:
        return self.permission_errors + self.file_not_found_errors + self.other_os_errors

    def record_os_error(self, path: str, exc: OSError) -> None:
        if isinstance(exc, PermissionError):
            kind = "permission_error"
        elif isinstance(exc, FileNotFoundError):
            kind = "file_not_found"
        else:
            kind = "os_error"

        key = (kind, os.path.abspath(path))
        if key in self._seen_error_keys:
            return
        self._seen_error_keys.add(key)

        if kind == "permission_error":
            self.permission_errors += 1
        elif kind == "file_not_found":
            self.file_not_found_errors += 1
        else:
            self.other_os_errors += 1

        if len(self.samples) < self.sample_limit:
            self.samples.append({"kind": kind, "path": path, "error": f"{type(exc).__name__}: {exc}"})
        else:
            self.samples_truncated = True

    def to_dict(self, unknown_inode_files: int) -> dict[str, Any]:
        return {
            "permission_errors": self.permission_errors,
            "file_not_found_errors": self.file_not_found_errors,
            "other_os_errors": self.other_os_errors,
            "total_errors": self.total_errors,
            "symlinks_skipped": self.symlinks_skipped,
            "cross_device_skipped": self.cross_device_skipped,
            "max_depth_skipped": self.max_depth_skipped,
            "special_files_skipped": self.special_files_skipped,
            "unknown_inode_files": unknown_inode_files,
            "sample_limit": self.sample_limit,
            "samples_truncated": self.samples_truncated,
            "samples": self.samples,
        }


def _close_quiet(fd: int | None) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


def sanitize_text(value: str) -> str:
    """Replace all UTF-16 surrogate code points (U+D800..U+DFFF) with U+FFFD."""
    try:
        value.encode("utf-8")
        return value
    except UnicodeEncodeError:
        return "".join("\ufffd" if 0xD800 <= ord(ch) <= 0xDFFF else ch for ch in value)


def sanitize_obj(obj: Any) -> Any:
    """Recursively sanitize strings in a report structure for strict UTF-8/JSON safety."""
    if isinstance(obj, str):
        return sanitize_text(obj)
    if isinstance(obj, dict):
        return {sanitize_text(k) if isinstance(k, str) else k: sanitize_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_obj(item) for item in obj]
    return obj


def _dir_identity(st: os.stat_result) -> tuple[int, int] | None:
    return (st.st_dev, st.st_ino) if st.st_ino != 0 else None


def _is_within_path(target: str, parent: str) -> bool:
    try:
        return os.path.commonpath([target, parent]) == parent
    except ValueError:
        return False


def _to_posix_rel(path: str, base: str) -> str:
    rel = os.path.relpath(path, base)
    return "." if rel == "." else rel.replace(os.sep, "/")


def extract_extension(filename: str) -> str:
    """Extract normalized lowercase extension using an explicit compound-suffix whitelist."""
    stripped = sanitize_text(filename).lower().lstrip(".")
    if "." not in stripped:
        return "(no_ext)"
    parts = stripped.split(".")
    last = parts[-1]
    if not last:
        return "(no_ext)"
    if len(parts) >= 3:
        compound = f".{parts[-2]}.{last}"
        if compound in COMPOUND_EXTENSIONS:
            return compound
    return "." + last


def bucket_index_for_size(size: int) -> int:
    for idx, (_, upper) in enumerate(SIZE_BUCKET_SPECS):
        if upper is None or size < upper:
            return idx
    return len(SIZE_BUCKET_SPECS) - 1


def compute_percentiles(
    unique_sizes: list[int],
    truncated: bool,
    truncation_reasons: list[str],
    unknown_inode_files: int,
) -> dict[str, Any]:
    count = len(unique_sizes)
    notes = [
        (
            f"Scan was truncated ({', '.join(truncation_reasons)}); percentiles reflect "
            "only the observed subset of unique regular files, not the full tree."
        )
        if truncated
        else (
            "Nearest-rank percentiles over observed unique regular files across scanned "
            "roots (excluding active exclusions, skipped entries, and unreadable paths)."
        )
    ]
    if unknown_inode_files > 0:
        notes.append(
            f"{unknown_inode_files} file(s) with st_ino == 0 could not be physically "
            "deduplicated and are excluded from unique percentiles."
        )
    scope_note = " ".join(notes)

    if count == 0:
        return {
            "denominator": "observed_unique_regular_files",
            "count": 0,
            "method": "nearest_rank",
            "scope_note": scope_note,
            "p50": None,
            "p90": None,
            "p99": None,
            "max": None,
        }

    ordered = sorted(unique_sizes)
    rank = lambda q: ordered[min(count - 1, max(0, math.ceil(q * count) - 1))]
    return {
        "denominator": "observed_unique_regular_files",
        "count": count,
        "method": "nearest_rank",
        "scope_note": scope_note,
        "p50": rank(0.50),
        "p90": rank(0.90),
        "p99": rank(0.99),
        "max": ordered[-1],
    }


def _validate_explicit_root(raw_path: str, flag_name: str) -> tuple[str, os.stat_result]:
    resolved = os.path.realpath(raw_path)
    try:
        st = os.lstat(resolved)
    except OSError as exc:
        raise CLIUsageError(f"{flag_name} path does not exist or is not accessible: {raw_path} ({exc})") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise CLIUsageError(f"{flag_name} path is not a directory: {raw_path}")
    if st.st_ino == 0:
        raise CapabilityError(
            f"{flag_name} filesystem does not provide verifiable directory inodes (st_ino == 0): {raw_path}; "
            "fall back to static audit sampling per pipeline-efficiency instructions."
        )
    return resolved, st


def resolve_roots(
    root_arg: str,
    artifact_dir_args: list[str],
) -> tuple[RootSpec, list[RootSpec], list[dict[str, str]]]:
    repo_resolved, repo_st = _validate_explicit_root(root_arg, "--root")
    repo_root = RootSpec(
        kind="repo_root",
        input_path=root_arg,
        resolved_path=repo_resolved,
        device_id=repo_st.st_dev,
        dir_key=_dir_identity(repo_st),
        repo_rel_prefix=".",
    )

    roots: list[RootSpec] = [repo_root]
    seen_paths: set[str] = {repo_resolved}
    seen_keys: set[tuple[int, int]] = {repo_root.dir_key} if repo_root.dir_key is not None else set()

    for raw_art in artifact_dir_args:
        art_resolved, art_st = _validate_explicit_root(raw_art, "--artifact-dir")
        art_key = _dir_identity(art_st)
        if art_resolved in seen_paths or (art_key is not None and art_key in seen_keys):
            continue
        seen_paths.add(art_resolved)
        if art_key is not None:
            seen_keys.add(art_key)
        rel_prefix = _to_posix_rel(art_resolved, repo_resolved) if _is_within_path(art_resolved, repo_resolved) else None
        roots.append(RootSpec(
            kind="artifact_dir",
            input_path=raw_art,
            resolved_path=art_resolved,
            device_id=art_st.st_dev,
            dir_key=art_key,
            repo_rel_prefix=rel_prefix,
        ))

    overlapping_roots = [
        {
            "ancestor": a.resolved_path,
            "descendant": b.resolved_path,
            "note": (
                "Descendant root is skipped during ancestor traversal and scanned "
                "under its own root entry to avoid double-counting."
            ),
        }
        for i, a in enumerate(roots)
        for j, b in enumerate(roots)
        if i != j and _is_within_path(b.resolved_path, a.resolved_path)
    ]
    return repo_root, roots, overlapping_roots


def resolve_explicit_script_dirs(
    repo_root: RootSpec,
    raw_root_arg: str,
    explicit_args: list[str],
) -> dict[str, ScriptDirAccum]:
    """Validate explicit --script-dir paths strictly inside repo_root with zero '..' or symlink traversal."""
    result: dict[str, ScriptDirAccum] = {}
    root_real = repo_root.resolved_path
    abs_input_root = os.path.abspath(raw_root_arg)
    ext_hint = "use --artifact-dir for approved external roots"
    seps = {"/", os.sep}
    if os.altsep:
        seps.add(os.altsep)

    for raw_dir in explicit_args:
        if not raw_dir or not raw_dir.strip():
            raise CLIUsageError("--script-dir cannot be empty")

        # Reject any '..' segment on the raw path BEFORE any normalization.
        unified = raw_dir
        for sep in seps:
            if sep != "/":
                unified = unified.replace(sep, "/")
        if ".." in unified.split("/"):
            raise CLIUsageError(f"--script-dir must not contain '..' path segments: {raw_dir} ({ext_hint})")

        # Collapse redundant consecutive slashes (including POSIX leading '//') consistently.
        collapsed = unified
        if os.sep == "/":
            while "//" in collapsed:
                collapsed = collapsed.replace("//", "/")
        candidate = collapsed if os.sep == "/" else collapsed.replace("/", os.sep)

        if os.path.isabs(candidate):
            norm_abs = os.path.normpath(candidate)
            if _is_within_path(norm_abs, root_real):
                raw_rel = os.path.relpath(norm_abs, root_real)
            elif _is_within_path(norm_abs, abs_input_root):
                raw_rel = os.path.relpath(norm_abs, abs_input_root)
            else:
                raise CLIUsageError(f"--script-dir must stay within --root ({root_real}); {ext_hint}: {raw_dir}")
        else:
            raw_rel = candidate

        # Segment-by-segment lstat verification blocks symlinks, non-dirs, and cross-device mounts.
        cur = root_real
        for seg in raw_rel.replace(os.sep, "/").split("/"):
            if not seg or seg == ".":
                continue
            cur = os.path.join(cur, seg)
            try:
                lst = os.lstat(cur)
            except OSError as exc:
                raise CLIUsageError(f"--script-dir path does not exist or is not accessible: {raw_dir} ({exc})") from exc
            if stat.S_ISLNK(lst.st_mode):
                raise CLIUsageError(f"--script-dir must not traverse or point to a symlink: {raw_dir} ({ext_hint})")
            if not stat.S_ISDIR(lst.st_mode):
                raise CLIUsageError(f"--script-dir path is not a directory: {raw_dir}")
            if lst.st_dev != repo_root.device_id:
                raise CLIUsageError(f"--script-dir crosses filesystem device boundary: {raw_dir} ({ext_hint})")

        if not _is_within_path(os.path.realpath(cur), root_real):
            raise CLIUsageError(f"--script-dir resolves outside --root ({root_real}); {ext_hint}: {raw_dir}")

        rel_posix = _to_posix_rel(cur, root_real)
        if rel_posix not in result:
            result[rel_posix] = ScriptDirAccum(path=rel_posix, source="explicit")

    return result


def _open_root_safe(
    root_spec: RootSpec,
) -> tuple[int | None, str | None, OSError | None, tuple[int, int] | None]:
    """Open a scan root with O_NOFOLLOW + O_DIRECTORY and verify its (st_dev, st_ino)."""
    if root_spec.dir_key is None or root_spec.dir_key[1] == 0:
        return None, None, OSError("Cannot verify root directory identity (st_ino == 0)"), None

    open_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        fd = os.open(root_spec.resolved_path, open_flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return None, "symlink", None, None
        if exc.errno in (errno.ENOTDIR, getattr(errno, "EMLINK", -1)):
            try:
                if stat.S_ISLNK(os.lstat(root_spec.resolved_path).st_mode):
                    return None, "symlink", None, None
            except OSError:
                pass
        return None, None, exc, None

    try:
        fst = os.fstat(fd)
    except OSError as exc:
        _close_quiet(fd)
        return None, None, exc, None

    if not stat.S_ISDIR(fst.st_mode):
        _close_quiet(fd)
        return None, None, OSError(errno.ENOTDIR, "Root is no longer a directory"), None
    if fst.st_dev != root_spec.device_id:
        _close_quiet(fd)
        return None, "cross_device", None, None
    if fst.st_ino == 0 or (fst.st_dev, fst.st_ino) != root_spec.dir_key:
        _close_quiet(fd)
        return None, None, OSError("Root directory identity changed concurrently"), None
    return fd, None, None, (fst.st_dev, fst.st_ino)


def _open_dir_anchored(
    root_fd: int,
    rel_parts: tuple[str, ...],
    root_dev: int,
    expected_ino: int | None,
) -> tuple[int | None, bool, str | None, OSError | None, tuple[int, int] | None]:
    """Open a directory anchored at root_fd via O_NOFOLLOW + fstat identity verification.

    Returns (fd, should_close_fd, skip_kind, os_error, opened_dir_key).
    """
    if not expected_ino:
        return None, False, None, OSError("Cannot verify directory identity (st_ino == 0)"), None

    if not rel_parts:
        try:
            fst = os.fstat(root_fd)
        except OSError as exc:
            return None, False, None, exc, None
        if not stat.S_ISDIR(fst.st_mode):
            return None, False, None, OSError(errno.ENOTDIR, "Root is no longer a directory"), None
        if fst.st_dev != root_dev:
            return None, False, "cross_device", None, None
        if fst.st_ino == 0 or fst.st_ino != expected_ino:
            return None, False, None, OSError("Root directory identity changed concurrently"), None
        return root_fd, False, None, None, (fst.st_dev, fst.st_ino)

    open_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    cur_fd = root_fd
    owns_cur = False
    last_idx = len(rel_parts) - 1

    try:
        for idx, part in enumerate(rel_parts):
            try:
                next_fd = os.open(part, open_flags, dir_fd=cur_fd)
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    return None, False, "symlink", None, None
                if exc.errno in (errno.ENOTDIR, getattr(errno, "EMLINK", -1)) and os.stat in os.supports_dir_fd:
                    try:
                        if stat.S_ISLNK(os.stat(part, dir_fd=cur_fd, follow_symlinks=False).st_mode):
                            return None, False, "symlink", None, None
                    except OSError:
                        pass
                return None, False, None, exc, None
            if owns_cur:
                _close_quiet(cur_fd)
            cur_fd, owns_cur = next_fd, True

            fst = os.fstat(cur_fd)
            if not stat.S_ISDIR(fst.st_mode):
                return None, False, None, OSError(errno.ENOTDIR, "Target is no longer a directory"), None
            if fst.st_dev != root_dev:
                return None, False, "cross_device", None, None
            if fst.st_ino == 0:
                return None, False, None, OSError("Cannot verify directory identity (st_ino == 0)"), None
            if idx == last_idx and fst.st_ino != expected_ino:
                return None, False, None, OSError("Directory identity changed concurrently during traversal"), None

        result_fd = cur_fd
        owns_cur = False
        return result_fd, True, None, None, (fst.st_dev, fst.st_ino)
    except OSError as exc:
        return None, False, None, exc, None
    finally:
        if owns_cur:
            _close_quiet(cur_fd)


def _update_script_dirs_for_dir(script_dirs: dict[str, ScriptDirAccum], repo_rel: str, dir_name: str) -> None:
    lower_name = dir_name.lower()
    if lower_name not in ARCHIVE_SUBDIR_NAMES:
        return
    for sd in script_dirs.values():
        if (sd.path == "." and repo_rel != ".") or repo_rel.startswith(sd.path + "/"):
            sd.archive_subdirs.setdefault(repo_rel, {"path": repo_rel, "name": lower_name, "logical_files": 0})


def _update_script_dirs_for_file(script_dirs: dict[str, ScriptDirAccum], repo_rel: str, fsize: int, ext: str) -> None:
    for sd in script_dirs.values():
        if sd.path == ".":
            sub_rel = repo_rel
        elif repo_rel.startswith(sd.path + "/"):
            sub_rel = repo_rel[len(sd.path) + 1 :]
        else:
            continue

        sd.logical_files += 1
        sd.logical_bytes += fsize
        if ext in COMMON_CODE_EXTENSIONS:
            sd.code_extensions[ext] += 1
        else:
            sd.non_code_files += 1

        dir_parts = sub_rel.split("/")[:-1]
        if dir_parts:
            cur = "" if sd.path == "." else sd.path
            in_archive = False
            for part in dir_parts:
                cur = part if not cur else f"{cur}/{part}"
                low = part.lower()
                if low in ARCHIVE_SUBDIR_NAMES:
                    in_archive = True
                    rec = sd.archive_subdirs.setdefault(cur, {"path": cur, "name": low, "logical_files": 0})
                    rec["logical_files"] += 1
            if in_archive:
                sd.archive_logical_files += 1


def _compute_repo_rel(root_spec: RootSpec, rel_in_root: str) -> str | None:
    if root_spec.repo_rel_prefix is None:
        return None
    if root_spec.repo_rel_prefix == ".":
        return rel_in_root
    return root_spec.repo_rel_prefix if rel_in_root == "." else f"{root_spec.repo_rel_prefix}/{rel_in_root}"


def run_probe(
    repo_root: RootSpec,
    roots: list[RootSpec],
    overlapping_roots: list[dict[str, str]],
    script_dirs: dict[str, ScriptDirAccum],
    excluded_dirs: frozenset[str],
    max_files: int,
    max_depth: int,
    timeout_seconds: float,
    top_n: int,
    start_mono: float | None = None,
) -> dict[str, Any]:
    if not SUPPORTS_FD_SCANDIR:
        raise CapabilityError(
            "Platform lacks safe dir_fd + O_NOFOLLOW + O_DIRECTORY directory traversal support; "
            "fall back to static audit sampling per pipeline-efficiency instructions."
        )
    if start_mono is None:
        start_mono = time.monotonic()
    deadline = start_mono + timeout_seconds

    gaps = GapTracker(sample_limit=MAX_GAP_SAMPLES)
    truncation_set: set[str] = set()
    explicit_root_keys = {r.dir_key for r in roots if r.dir_key is not None}
    explicit_root_paths = {r.resolved_path for r in roots}

    visited_dir_ids: set[tuple[int, int]] = set()
    seen_file_ids: set[tuple[int, int]] = set()

    entries_visited = directories_scanned = 0
    logical_files = logical_bytes = unique_files = unique_bytes = 0
    known_inode_logical_files = unknown_inode_files = 0
    allocated_bytes_total = excluded_dirs_skipped = 0
    allocated_bytes_available = HAS_ST_BLOCKS

    bucket_files = [0] * len(SIZE_BUCKET_SPECS)
    bucket_bytes = [0] * len(SIZE_BUCKET_SPECS)
    unique_sizes: list[int] = []
    ext_stats: dict[str, ExtAccum] = {}
    dir_accums: list[DirAccum] = []

    stop_all = False

    for root_spec in roots:
        if stop_all:
            break
        if time.monotonic() >= deadline:
            truncation_set.add("timeout")
            break
        if entries_visited >= max_files:
            truncation_set.add("max_files")
            break

        root_dir_idx = len(dir_accums)
        dir_accums.append(DirAccum(root_path=root_spec.resolved_path, rel_path=".", parent_idx=None, depth=0))

        if max_depth == 0:
            gaps.max_depth_skipped += 1
            truncation_set.add("max_depth")
            continue

        root_fd, root_skip, root_err, root_key = _open_root_safe(root_spec)
        if root_skip == "symlink":
            gaps.symlinks_skipped += 1
            continue
        if root_skip == "cross_device":
            gaps.cross_device_skipped += 1
            continue
        if root_err is not None or root_fd is None or root_key is None:
            if root_err is not None:
                gaps.record_os_error(root_spec.resolved_path, root_err)
            continue

        root_seen_inodes: set[tuple[int, int]] = set()
        queue: collections.deque[tuple[str, tuple[str, ...], int, int, int | None]] = collections.deque([
            (root_spec.resolved_path, (), root_dir_idx, 0, root_key[1])
        ])

        try:
            while queue and not stop_all:
                if time.monotonic() >= deadline:
                    truncation_set.add("timeout")
                    stop_all = True
                    break
                if entries_visited >= max_files:
                    truncation_set.add("max_files")
                    stop_all = True
                    break

                dir_path, rel_parts, dir_idx, dir_depth, expected_ino = queue.popleft()
                dir_node = dir_accums[dir_idx]

                if expected_ino and (root_spec.device_id, expected_ino) in visited_dir_ids:
                    continue

                opened_fd, close_opened_fd, skip_kind, open_err, opened_key = _open_dir_anchored(
                    root_fd, rel_parts, root_spec.device_id, expected_ino
                )
                if skip_kind == "symlink":
                    gaps.symlinks_skipped += 1
                    continue
                if skip_kind == "cross_device":
                    gaps.cross_device_skipped += 1
                    continue
                if open_err is not None or opened_fd is None:
                    if open_err is not None:
                        gaps.record_os_error(dir_path, open_err)
                    continue

                try:
                    if opened_key is not None and opened_key in visited_dir_ids:
                        continue
                    with os.scandir(opened_fd) as it:
                        # Commit visited directory identity only once opened and confirmed scannable.
                        if opened_key is not None:
                            visited_dir_ids.add(opened_key)
                        directories_scanned += 1
                        root_spec.directories_scanned += 1
                        for entry in it:
                            if time.monotonic() >= deadline:
                                truncation_set.add("timeout")
                                stop_all = True
                                break
                            if entries_visited >= max_files:
                                truncation_set.add("max_files")
                                stop_all = True
                                break

                            entries_visited += 1
                            root_spec.entries_visited += 1
                            dir_node.direct_entries += 1

                            entry_name = entry.name
                            entry_full_path = os.path.join(dir_path, entry_name)
                            entry_rel = entry_name if dir_node.rel_path == "." else f"{dir_node.rel_path}/{entry_name}"

                            try:
                                if entry.is_symlink():
                                    gaps.symlinks_skipped += 1
                                    continue
                                st = entry.stat(follow_symlinks=False)
                                if stat.S_ISLNK(st.st_mode):
                                    gaps.symlinks_skipped += 1
                                    continue
                            except OSError as exc:
                                gaps.record_os_error(entry_full_path, exc)
                                continue

                            mode = st.st_mode
                            is_dir = stat.S_ISDIR(mode)
                            dkey = _dir_identity(st) if is_dir else None

                            # 1. Explicit overlapping root check BEFORE device check.
                            if is_dir:
                                dir_node.direct_subdirs += 1
                                if (
                                    (entry_full_path in explicit_root_paths and entry_full_path != root_spec.resolved_path)
                                    or (dkey is not None and dkey in explicit_root_keys and dkey != root_spec.dir_key)
                                ):
                                    if (
                                        root_spec is repo_root
                                        and dir_depth == 0
                                        and entry_name in DEFAULT_SCRIPT_DIR_NAMES
                                        and entry_name not in excluded_dirs
                                        and entry_name not in script_dirs
                                    ):
                                        script_dirs[entry_name] = ScriptDirAccum(path=entry_name, source="auto")
                                    continue

                                # 2. Active directory exclusion check.
                                if entry_name in excluded_dirs:
                                    excluded_dirs_skipped += 1
                                    continue

                            # 3. Same-device boundary for ALL entry types (dirs, regular files, special).
                            if st.st_dev != root_spec.device_id:
                                gaps.cross_device_skipped += 1
                                continue

                            if is_dir:
                                if (
                                    root_spec is repo_root
                                    and dir_depth == 0
                                    and entry_name in DEFAULT_SCRIPT_DIR_NAMES
                                    and entry_name not in script_dirs
                                ):
                                    script_dirs[entry_name] = ScriptDirAccum(path=entry_name, source="auto")

                                repo_rel = _compute_repo_rel(root_spec, entry_rel)
                                if repo_rel is not None and script_dirs:
                                    _update_script_dirs_for_dir(script_dirs, repo_rel, entry_name)

                                # Skip already-scanned dirs; commit visited_dir_ids only after open succeeds at dequeue.
                                if dkey is not None and dkey in visited_dir_ids:
                                    continue
                                entry_depth = dir_depth + 1
                                if entry_depth >= max_depth:
                                    gaps.max_depth_skipped += 1
                                    truncation_set.add("max_depth")
                                    continue

                                child_idx = len(dir_accums)
                                dir_accums.append(DirAccum(root_spec.resolved_path, entry_rel, dir_idx, entry_depth))
                                child_ino = dkey[1] if dkey is not None else None
                                queue.append((entry_full_path, rel_parts + (entry_name,), child_idx, entry_depth, child_ino))

                            elif stat.S_ISREG(mode):
                                fsize = st.st_size
                                logical_files += 1
                                logical_bytes += fsize
                                root_spec.logical_files += 1
                                root_spec.logical_bytes += fsize
                                dir_node.direct_logical_files += 1
                                dir_node.direct_logical_bytes += fsize

                                ext = extract_extension(entry_name)
                                eacc = ext_stats.get(ext)
                                if eacc is None:
                                    eacc = ExtAccum()
                                    ext_stats[ext] = eacc
                                eacc.logical_files += 1
                                eacc.logical_bytes += fsize

                                repo_rel = _compute_repo_rel(root_spec, entry_rel)
                                if repo_rel is not None and script_dirs:
                                    _update_script_dirs_for_file(script_dirs, repo_rel, fsize, ext)

                                if st.st_ino == 0:
                                    unknown_inode_files += 1
                                    root_spec.unknown_inode_files += 1
                                else:
                                    fid = (st.st_dev, st.st_ino)
                                    known_inode_logical_files += 1
                                    root_spec.known_inode_logical_files += 1
                                    if fid not in root_seen_inodes:
                                        root_seen_inodes.add(fid)
                                        root_spec.unique_files += 1
                                        root_spec.unique_bytes += fsize
                                    if fid not in seen_file_ids:
                                        seen_file_ids.add(fid)
                                        unique_files += 1
                                        unique_bytes += fsize
                                        bidx = bucket_index_for_size(fsize)
                                        bucket_files[bidx] += 1
                                        bucket_bytes[bidx] += fsize
                                        unique_sizes.append(fsize)
                                        if allocated_bytes_available:
                                            blocks = getattr(st, "st_blocks", None)
                                            if blocks is None:
                                                allocated_bytes_available = False
                                            else:
                                                allocated_bytes_total += blocks * 512
                            else:
                                gaps.special_files_skipped += 1
                except OSError as exc:
                    gaps.record_os_error(dir_path, exc)
                finally:
                    if close_opened_fd:
                        _close_quiet(opened_fd)
        finally:
            _close_quiet(root_fd)

    # Bottom-up subtree rollup via reverse BFS order (O(D), non-recursive).
    for d in dir_accums:
        d.total_paths = d.direct_entries
        d.total_logical_files = d.direct_logical_files
        d.total_logical_bytes = d.direct_logical_bytes
    for idx in range(len(dir_accums) - 1, -1, -1):
        d = dir_accums[idx]
        if d.parent_idx is not None:
            parent = dir_accums[d.parent_idx]
            parent.total_paths += d.total_paths
            parent.total_logical_files += d.total_logical_files
            parent.total_logical_bytes += d.total_logical_bytes

    elapsed_seconds = round(time.monotonic() - start_mono, 4)
    truncation_reasons = [r for r in TRUNCATION_REASON_ORDER if r in truncation_set]
    truncated = len(truncation_reasons) > 0
    has_gaps = (
        gaps.total_errors > 0
        or gaps.symlinks_skipped > 0
        or gaps.cross_device_skipped > 0
        or gaps.max_depth_skipped > 0
        or gaps.special_files_skipped > 0
        or unknown_inode_files > 0
    )
    coverage_status = "TRUNCATED" if truncated else ("PARTIAL" if has_gaps else "COMPLETE")

    report = {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "roots": [
            {
                "kind": r.kind,
                "input_path": r.input_path,
                "path": r.resolved_path,
                "device_id": r.device_id,
                "entries_visited": r.entries_visited,
                "directories_scanned": r.directories_scanned,
                "logical_files": r.logical_files,
                "logical_bytes": r.logical_bytes,
                "unique_files": r.unique_files,
                "unique_bytes": r.unique_bytes,
                "hardlink_duplicates": r.known_inode_logical_files - r.unique_files,
                "unknown_inode_files": r.unknown_inode_files,
            }
            for r in roots
        ],
        "overlapping_roots": overlapping_roots,
        "limits": {"max_files": max_files, "max_depth": max_depth, "timeout_seconds": timeout_seconds, "top_n": top_n},
        "excluded_dirs": sorted(excluded_dirs),
        "truncated": truncated,
        "truncation_reasons": truncation_reasons,
        "summary": {
            "coverage_status": coverage_status,
            "elapsed_seconds": elapsed_seconds,
            "entries_visited": entries_visited,
            "directories_scanned": directories_scanned,
            "logical_files": logical_files,
            "logical_bytes": logical_bytes,
            "unique_files": unique_files,
            "unique_bytes": unique_bytes,
            "hardlink_duplicates": known_inode_logical_files - unique_files,
            "unknown_inode_files": unknown_inode_files,
            "allocated_bytes": allocated_bytes_total if allocated_bytes_available else None,
            "allocated_bytes_note": (
                "512-byte block allocation estimate (sum of st_blocks * 512 across unique regular "
                "inodes; null if platform lacks st_blocks). Reflects filesystem allocation, sparse "
                "extents, compression, or inline data without assuming fragmentation cause."
            ),
            "symlinks_skipped": gaps.symlinks_skipped,
            "cross_device_skipped": gaps.cross_device_skipped,
            "excluded_dirs_skipped": excluded_dirs_skipped,
            "special_files_skipped": gaps.special_files_skipped,
        },
        "size_buckets": {
            label: {"unique_files": bucket_files[idx], "unique_bytes": bucket_bytes[idx]}
            for idx, (label, _) in enumerate(SIZE_BUCKET_SPECS)
        },
        "size_percentiles": compute_percentiles(unique_sizes, truncated, truncation_reasons, unknown_inode_files),
        "top_extensions": [
            {"extension": ext, "logical_files": acc.logical_files, "logical_bytes": acc.logical_bytes}
            for ext, acc in sorted(ext_stats.items(), key=lambda kv: (-kv[1].logical_files, -kv[1].logical_bytes, kv[0]))[:top_n]
        ],
        "top_dirs_by_files": [
            {
                "root": d.root_path,
                "path": d.rel_path,
                "direct_logical_files": d.direct_logical_files,
                "direct_entries": d.direct_entries,
                "total_logical_files": d.total_logical_files,
                "total_paths": d.total_paths,
                "direct_logical_bytes": d.direct_logical_bytes,
                "total_logical_bytes": d.total_logical_bytes,
            }
            for d in sorted(
                (d for d in dir_accums if d.direct_logical_files > 0 or d.total_paths > 0),
                key=lambda d: (-d.direct_logical_files, -d.total_paths, -d.direct_logical_bytes, d.root_path, d.rel_path),
            )[:top_n]
        ],
        "top_dirs_by_bytes": [
            {
                "root": d.root_path,
                "path": d.rel_path,
                "direct_logical_bytes": d.direct_logical_bytes,
                "total_logical_bytes": d.total_logical_bytes,
                "direct_logical_files": d.direct_logical_files,
                "total_logical_files": d.total_logical_files,
                "total_paths": d.total_paths,
            }
            for d in sorted(
                (d for d in dir_accums if d.direct_logical_bytes > 0 or d.total_logical_bytes > 0),
                key=lambda d: (-d.direct_logical_bytes, -d.total_logical_bytes, -d.direct_logical_files, d.root_path, d.rel_path),
            )[:top_n]
        ],
        "script_dirs": [
            {
                "path": sd.path,
                "source": sd.source,
                "logical_files": sd.logical_files,
                "logical_bytes": sd.logical_bytes,
                "code_extensions": dict(sorted(sd.code_extensions.items(), key=lambda kv: (-kv[1], kv[0]))),
                "non_code_files": sd.non_code_files,
                "has_archive_subdirs": len(sd.archive_subdirs) > 0,
                "archive_logical_files": sd.archive_logical_files,
                "archive_subdirs": sorted(sd.archive_subdirs.values(), key=lambda item: item["path"]),
            }
            for sd in sorted(script_dirs.values(), key=lambda s: (0 if s.source == "explicit" else 1, s.path))
        ],
        "coverage_gaps": gaps.to_dict(unknown_inode_files),
    }
    return sanitize_obj(report)


def _fmt_bytes(num_bytes: int | None) -> str:
    if num_bytes is None:
        return "n/a"
    val = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if val < 1024.0 or unit == "TiB":
            return f"{int(val)} B" if unit == "B" else f"{val:.2f} {unit}"
        val /= 1024.0
    return f"{num_bytes} B"


def render_text_summary(report: dict[str, Any]) -> str:
    s, p, g = report["summary"], report["size_percentiles"], report["coverage_gaps"]
    detail = f" ({', '.join(report['truncation_reasons'])})" if report["truncated"] else ""
    lines = [
        f"[scan_artifacts v{report['schema_version']}] status={s['coverage_status']}{detail} elapsed={s['elapsed_seconds']}s",
        "Roots:",
        *(
            f"  - [{r['kind']}] {r['path']} (dev={r['device_id']}, entries={r['entries_visited']}, "
            f"logical={r['logical_files']}/{_fmt_bytes(r['logical_bytes'])}, unique={r['unique_files']})"
            for r in report["roots"]
        ),
    ]
    if report["overlapping_roots"]:
        lines.append(f"Overlapping roots: {len(report['overlapping_roots'])} ancestor/descendant pair(s)")
    lines.extend([
        f"Summary: entries={s['entries_visited']}, dirs_scanned={s['directories_scanned']}, "
        f"logical={s['logical_files']} ({_fmt_bytes(s['logical_bytes'])}), "
        f"unique={s['unique_files']} ({_fmt_bytes(s['unique_bytes'])}), "
        f"hardlinks={s['hardlink_duplicates']}, unknown_inode={s['unknown_inode_files']}, "
        f"allocated_est={_fmt_bytes(s['allocated_bytes'])}",
        f"Gaps & Exclusions: os_errors={g['total_errors']} (perm={g['permission_errors']}, "
        f"not_found={g['file_not_found_errors']}, other={g['other_os_errors']}), "
        f"symlinks={g['symlinks_skipped']}, cross_device={g['cross_device_skipped']}, "
        f"max_depth={g['max_depth_skipped']}, special={s['special_files_skipped']}, "
        f"excluded_dirs={s['excluded_dirs_skipped']}",
        f"Percentiles (n={p['count']}, {p['method']}): p50={_fmt_bytes(p['p50'])}, "
        f"p90={_fmt_bytes(p['p90'])}, p99={_fmt_bytes(p['p99'])}, max={_fmt_bytes(p['max'])}",
        "Size buckets (unique regular files):",
        *(f"  {lbl:>11s}: {b['unique_files']:>8d} files ({_fmt_bytes(b['unique_bytes'])})" for lbl, b in report["size_buckets"].items()),
    ])
    if report["top_extensions"]:
        lines.append("Top extensions (logical):")
        lines.extend(f"  {i['extension']:<14s} files={i['logical_files']} ({_fmt_bytes(i['logical_bytes'])})" for i in report["top_extensions"])
    if report["top_dirs_by_files"]:
        lines.append("Top dirs by direct logical files:")
        lines.extend(
            f"  {i['path']:<36s} direct={i['direct_logical_files']} total_paths={i['total_paths']} bytes={_fmt_bytes(i['direct_logical_bytes'])}"
            for i in report["top_dirs_by_files"]
        )
    if report["top_dirs_by_bytes"]:
        lines.append("Top dirs by direct logical bytes:")
        lines.extend(
            f"  {i['path']:<36s} direct={_fmt_bytes(i['direct_logical_bytes'])} total={_fmt_bytes(i['total_logical_bytes'])} files={i['direct_logical_files']}"
            for i in report["top_dirs_by_bytes"]
        )
    if report["script_dirs"]:
        lines.append("Script dirs (metadata only):")
        for sd in report["script_dirs"]:
            arch = f"archive_files={sd['archive_logical_files']}" if sd["has_archive_subdirs"] else "no_archive"
            lines.append(f"  - {sd['path']} ({sd['source']}): logical_files={sd['logical_files']} ({_fmt_bytes(sd['logical_bytes'])}), {arch}")
    return "\n".join(lines)


def _safe_write(stream: Any, text: str) -> None:
    clean = sanitize_text(text)
    enc = getattr(stream, "encoding", None) or "utf-8"
    try:
        stream.write(clean + "\n")
    except UnicodeEncodeError:
        stream.write(clean.encode(enc, errors="replace").decode(enc, errors="replace") + "\n")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Bounded read-only filesystem metadata probe for artifact/pipeline audits "
            "(pure stdlib, zero file-content reads, objective observations only)."
        )
    )
    parser.add_argument("--root", default=".", help="Target repository root directory to probe (default: .).")
    parser.add_argument("--artifact-dir", action="append", default=[], help="Approved external artifact/data root directory to probe (repeatable).")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable UTF-8/ASCII JSON to stdout.")
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES, help=f"Hard global entry traversal budget (default: {DEFAULT_MAX_FILES}).")
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH, help=f"Maximum entry depth per scan root; 1 scans direct children only (default: {DEFAULT_MAX_DEPTH}).")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help=f"Cooperative traversal wall-clock limit in seconds (default: {DEFAULT_TIMEOUT_SECONDS}).")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N, help=f"Top-N leaderboard length (default: {DEFAULT_TOP_N}).")
    parser.add_argument(
        "--script-dir",
        action="append",
        default=[],
        help=(
            "Additional tool/script directory strictly within --root (no '..' segments or symlinks; repeatable). "
            "Use --artifact-dir for approved external roots. Auto-discovers scripts/, tools/, bin/, jobs/, pipelines/."
        ),
    )
    parser.add_argument("--exclude-dir", action="append", default=[], help="Additional directory basename to exclude from traversal (repeatable).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    start_mono = time.monotonic()

    try:
        if args.max_files < 1:
            raise CLIUsageError("--max-files must be >= 1")
        if args.max_depth < 0:
            raise CLIUsageError("--max-depth must be >= 0")
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise CLIUsageError("--timeout must be a finite number > 0")
        if args.top < 1:
            raise CLIUsageError("--top must be >= 1")

        if not SUPPORTS_FD_SCANDIR:
            raise CapabilityError(
                "Platform lacks safe dir_fd + O_NOFOLLOW + O_DIRECTORY directory traversal support; "
                "fall back to static audit sampling per pipeline-efficiency instructions."
            )

        repo_root, roots, overlapping_roots = resolve_roots(args.root, args.artifact_dir)
        script_dirs = resolve_explicit_script_dirs(repo_root, args.root, args.script_dir)
        excluded_dirs = frozenset(DEFAULT_EXCLUDE_DIRS).union(args.exclude_dir)

        report = run_probe(
            repo_root=repo_root,
            roots=roots,
            overlapping_roots=overlapping_roots,
            script_dirs=script_dirs,
            excluded_dirs=excluded_dirs,
            max_files=args.max_files,
            max_depth=args.max_depth,
            timeout_seconds=args.timeout,
            top_n=args.top,
            start_mono=start_mono,
        )
    except CLIUsageError as exc:
        _safe_write(sys.stderr, f"ERROR: {exc}")
        return 2
    except CapabilityError as exc:
        _safe_write(sys.stderr, f"ERROR: {exc}")
        return 3

    if args.json:
        sys.stdout.write(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    else:
        _safe_write(sys.stdout, render_text_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
