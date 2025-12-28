import logging
import os
import sys
import sysconfig
import time
from typing import Dict

import duckdb
import psutil
import timeout_decorator

from celery_app import app

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("gil_demo")

_TIMEOUT_ERROR = getattr(timeout_decorator, "TimeoutError", TimeoutError)


def _cleanup_children(timeout: float = 1.0) -> Dict[str, int]:
    proc = psutil.Process()
    children = proc.children(recursive=True)
    if not children:
        return {"terminated": 0, "killed": 0, "remaining": 0}

    targets = []
    for child in children:
        try:
            cmdline = " ".join(child.cmdline())
        except psutil.Error:
            cmdline = ""
        if "multiprocessing.resource_tracker" in cmdline:
            continue
        if "multiprocessing.forkserver" in cmdline:
            continue
        targets.append(child)

    if not targets:
        return {"terminated": 0, "killed": 0, "remaining": 0}

    for child in targets:
        try:
            child.terminate()
        except psutil.Error:
            continue

    gone, alive = psutil.wait_procs(targets, timeout=timeout)
    for child in alive:
        try:
            child.kill()
        except psutil.Error:
            continue

    gone_after_kill, alive_after_kill = psutil.wait_procs(alive, timeout=timeout)
    return {
        "terminated": len(gone) + len(gone_after_kill),
        "killed": len(alive),
        "remaining": len(alive_after_kill),
    }


def _gil_status() -> Dict[str, str]:
    build_flag = sysconfig.get_config_var("Py_GIL_DISABLED")
    build_free_threaded = bool(build_flag) if build_flag is not None else False
    gil_enabled = None
    is_gil_enabled = getattr(sys, "_is_gil_enabled", None)
    if callable(is_gil_enabled):
        try:
            gil_enabled = is_gil_enabled()
        except Exception:
            gil_enabled = None
    return {
        "build_free_threaded": str(build_free_threaded),
        "gil_enabled": str(gil_enabled),
        "python_version": sys.version.replace("\n", " "),
    }


def _child_process_summary() -> Dict[str, int]:
    proc = psutil.Process()
    children = proc.children(recursive=True)
    zombie_count = 0
    for child in children:
        try:
            if child.status() == psutil.STATUS_ZOMBIE:
                zombie_count += 1
        except psutil.Error:
            continue
    return {
        "children": len(children),
        "zombies": zombie_count,
    }


def _duckdb_query(rows: int) -> None:
    threads = int(os.getenv("DUCKDB_THREADS", "1"))
    conn = duckdb.connect(config={"threads": threads})
    try:
        conn.execute(f"SELECT sum(i) FROM range({rows}) t(i)").fetchall()
    finally:
        conn.close()


def _run_with_timeout(rows: int, timeout_sec: float, use_signals: bool) -> str:
    wrapped = timeout_decorator.timeout(timeout_sec, use_signals=use_signals)(_duckdb_query)
    wrapped(rows)
    return "completed"


@app.task(bind=True, name="tasks.leak_demo")
def leak_demo(self, iterations: int = 10, rows: int = 500_000_000, timeout_sec: float = 2.0) -> Dict[str, str]:
    use_signals = os.getenv("TIMEOUT_USE_SIGNALS", "0") == "1"
    mode = os.getenv("GIL_MODE", "unknown")
    cleanup_children = os.getenv("CLEANUP_CHILDREN", "0") == "1"

    logger.info("task_start mode=%s use_signals=%s cleanup_children=%s", mode, use_signals, cleanup_children)
    logger.info("gil_status %s", _gil_status())

    last_outcome = ""
    for i in range(iterations):
        start = time.monotonic()
        cleanup_result = None
        try:
            _run_with_timeout(rows, timeout_sec, use_signals)
            last_outcome = "ok"
        except _TIMEOUT_ERROR:
            last_outcome = "timeout"
            if cleanup_children:
                cleanup_result = _cleanup_children()
        except Exception as exc:
            last_outcome = f"error:{exc.__class__.__name__}"
        elapsed = time.monotonic() - start
        proc_info = _child_process_summary()
        logger.info(
            "iter=%s outcome=%s elapsed=%.2fs children=%s zombies=%s",
            i + 1,
            last_outcome,
            elapsed,
            proc_info["children"],
            proc_info["zombies"],
        )
        if cleanup_result is not None:
            logger.info(
                "cleanup terminated=%s killed=%s remaining=%s",
                cleanup_result["terminated"],
                cleanup_result["killed"],
                cleanup_result["remaining"],
            )
        time.sleep(0.2)

    return {
        "mode": mode,
        "last_outcome": last_outcome,
        "use_signals": str(use_signals),
    }
