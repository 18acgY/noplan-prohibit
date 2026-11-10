"""TradingAgents 分析 worker（宿主机，stdlib only）。

流程：
  1. 每 TA_HEARTBEAT_SEC 秒向网站发一次心跳（另起线程）
  2. 每 POLL_SEC 秒领取一个排队任务
  3. 用 TA_PYTHON 跑 TA_LAUNCHER，超时 TA_TIMEOUT_MIN 分钟
  4. 从结果目录读 decision.md / complete_report.md
  5. 回写 done / failed

只依赖标准库；真正的分析进程用 TradingAgents 自己的 venv。
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

API = os.environ.get("NOPLAN_API_BASE", "http://127.0.0.1:8080").rstrip("/")
TOKEN = os.environ.get("NOPLAN_WORKER_TOKEN", "")
TA_REPO = Path(os.environ.get("TA_REPO", str(Path.home() / "TradingAgents"))).expanduser()
TA_DATA = Path(os.environ.get("TA_DATA", str(TA_REPO.parent / ".runtime"))).expanduser()
TA_LAUNCHER = Path(os.environ.get("TA_LAUNCHER", str(TA_REPO.parent / "relay_launcher.py"))).expanduser()
TA_PYTHON = os.environ.get("TA_PYTHON", str(TA_REPO / ".venv" / "bin" / "python"))
TA_ANALYSTS = os.environ.get("TA_ANALYSTS", "market,news,fundamentals")
TA_TIMEOUT_MIN = int(os.environ.get("TA_TIMEOUT_MIN", "20"))
POLL_SEC = int(os.environ.get("TA_POLL_SEC", "5"))
HEARTBEAT_SEC = int(os.environ.get("TA_HEARTBEAT_SEC", "30"))
CANCEL_POLL_SEC = float(os.environ.get("TA_CANCEL_POLL_SEC", "5"))

REPORTS_SUBDIR = "reports"
_STOP = threading.Event()

_FIELD_RE = {
    "rating": re.compile(r"^\*\*Rating\*\*:\s*(.+?)\s*$", re.MULTILINE),
    "summary": re.compile(r"^\*\*Executive Summary\*\*:\s*(.+?)\s*$", re.MULTILINE),
    "target_price": re.compile(r"^\*\*Price Target\*\*:\s*(.+?)\s*$", re.MULTILINE),
    "horizon": re.compile(r"^\*\*Time Horizon\*\*:\s*(.+?)\s*$", re.MULTILINE),
}
_EMPTY_VALUES = {"", "not provided", "none", "n/a", "—", "-"}


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return None if text.lower() in _EMPTY_VALUES else text


def parse_decision(text: str) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for key, pattern in _FIELD_RE.items():
        match = pattern.search(text)
        result[key] = _clean(match.group(1)) if match else None
    return result


def find_report_dir(reports_root: Path, ticker: str, since: float) -> Path | None:
    """找该 ticker 在 since 之后生成的最新报告目录。"""
    if not reports_root.exists():
        return None
    candidates = [
        p
        for p in reports_root.iterdir()
        if p.is_dir() and p.name.startswith(f"{ticker}_") and p.stat().st_mtime >= since - 5
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: (p.name, p.stat().st_mtime))


def _post(path: str, payload: dict | None = None, timeout: int = 15) -> tuple[int, bytes]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _get(path: str, timeout: int = 10) -> tuple[int, str]:
    request = urllib.request.Request(
        f"{API}{path}",
        method="GET",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def cancel_requested(job_id: int) -> bool:
    """网站是否要求取消这个任务。网络异常时不误杀。"""
    status, body = _get(f"/internal/analysis/{job_id}/state")
    if status == 404:
        return True  # 任务已不存在，停掉别再烧钱
    if status != 200:
        return False
    try:
        return bool(json.loads(body).get("cancel_requested"))
    except (ValueError, AttributeError):
        return False


def terminate_process(proc: subprocess.Popen, grace: float = 10.0) -> None:
    """先给整个进程组 SIGTERM，grace 秒不退再 SIGKILL。"""
    if proc.poll() is not None:
        return
    if hasattr(os, "killpg"):
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except OSError:
            proc.terminate()
    else:
        proc.terminate()
    try:
        proc.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass
    if hasattr(os, "killpg"):
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            proc.kill()
    else:
        proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def wait_process(
    proc: subprocess.Popen, job_id: int, started: float, timeout_s: float
) -> tuple[str, int | None]:
    """等子进程结束，返回 (原因, 退出码)：finished / timeout / cancelled。"""
    while True:
        code = proc.poll()
        if code is not None:
            return "finished", code
        if cancel_requested(job_id):
            terminate_process(proc)
            return "cancelled", None
        if time.time() - started > timeout_s:
            terminate_process(proc)
            return "timeout", None
        time.sleep(CANCEL_POLL_SEC)


def heartbeat_loop() -> None:
    while not _STOP.is_set():
        try:
            _post("/internal/worker/heartbeat", {"version": "1"})
        except Exception as exc:  # noqa: BLE001 - 心跳失败不该杀 worker
            print(f"[worker] heartbeat failed: {exc}", flush=True)
        _STOP.wait(HEARTBEAT_SEC)


class ProgressTail:
    """增量读取 launcher 写的 JSONL 进度文件。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.offset = 0
        self.buffer = ""

    def poll(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                fh.seek(self.offset)
                chunk = fh.read()
                self.offset = fh.tell()
        except OSError:
            return []
        self.buffer += chunk
        *lines, rest = self.buffer.split("\n")
        self.buffer = rest
        events = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                events.append(value)
        return events


def progress_path(job_id: int, started: float) -> Path:
    return TA_DATA / "logs" / f"progress-{job_id}-{int(started)}.jsonl"


def subprocess_env(job_id: int, progress_file: Path) -> dict[str, str]:
    return {
        **os.environ,
        "TA_REPO": str(TA_REPO),
        "TA_DATA": str(TA_DATA),
        "PYTHONUTF8": "1",
        "TZ": "Asia/Shanghai",
        "NOPLAN_PROGRESS_FILE": str(progress_file),
    }


def forward_events(job_id: int, events: list[dict]) -> None:
    """把进度事件转发给网站；失败只记日志，不影响分析。"""
    if not events:
        return
    try:
        status, body = _post(
            f"/internal/analysis/{job_id}/progress",
            {"events": events},
            timeout=15,
        )
        if status != 200:
            print(f"[worker] progress rejected {status} {body[:120]!r}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[worker] progress forward failed: {exc}", flush=True)


def tail_loop(tail: ProgressTail, job_id: int, stop: threading.Event) -> None:
    while not stop.is_set():
        forward_events(job_id, tail.poll())
        stop.wait(1.0)
    # 收尾：把进程结束前写入的最后几行也送出去
    time.sleep(0.2)
    forward_events(job_id, tail.poll())


def run_analysis(job: dict) -> dict:
    """执行一次分析，返回 finish 请求体。"""
    ticker = job["ticker"]
    analysis_date = job["analysis_date"]
    reports_root = TA_DATA / "logs" / REPORTS_SUBDIR
    started = time.time()
    log_path = TA_DATA / "logs" / f"worker-{job['id']}-{int(started)}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    progress_file = progress_path(job["id"], started)

    cmd = [
        TA_PYTHON,
        str(TA_LAUNCHER),
        "--ticker",
        ticker,
        "--date",
        analysis_date,
        "--analysts",
        TA_ANALYSTS,
        "--no-checkpoint",
        "--save",
        "--no-show",
        "--no-html",
    ]
    env = subprocess_env(job["id"], progress_file)
    print(f"[worker] job={job['id']} ticker={ticker} date={analysis_date}", flush=True)
    tail = ProgressTail(progress_file)
    tail_stop = threading.Event()
    tail_thread = threading.Thread(
        target=tail_loop, args=(tail, job["id"], tail_stop), daemon=True
    )
    tail_thread.start()
    spawn_kwargs: dict = {}
    if hasattr(os, "setsid"):
        # POSIX：让分析进程自成进程组，取消时能连同子进程一起杀
        spawn_kwargs["start_new_session"] = True
    try:
        with open(log_path, "w", encoding="utf-8") as log:
            proc = subprocess.Popen(
                cmd,
                cwd=str(TA_REPO),
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                **spawn_kwargs,
            )
            reason, exit_code = wait_process(
                proc, job["id"], started, TA_TIMEOUT_MIN * 60
            )
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "run_dir": str(log_path),
            "duration_s": time.time() - started,
        }
    finally:
        # 先停 tail 再删文件，保证最后几条事件已经转发出去
        tail_stop.set()
        tail_thread.join(timeout=5)
        try:
            progress_file.unlink(missing_ok=True)
        except OSError:
            pass

    duration = time.time() - started
    if reason == "cancelled":
        return {
            "status": "cancelled",
            "run_dir": str(log_path),
            "duration_s": duration,
        }
    if reason == "timeout":
        return {
            "status": "failed",
            "error": f"超时（>{TA_TIMEOUT_MIN} 分钟），已终止",
            "run_dir": str(log_path),
            "duration_s": duration,
        }

    if exit_code == 0:
        forward_events(job["id"], [{"stage": "report"}])

    if exit_code != 0:
        tail = _tail(log_path)
        return {
            "status": "failed",
            "error": f"分析进程退出码 {exit_code}\n{tail}",
            "run_dir": str(log_path),
            "duration_s": duration,
        }

    report_dir = find_report_dir(reports_root, ticker, started)
    if report_dir is None:
        return {
            "status": "failed",
            "error": f"没有找到 {ticker} 的新报告目录（{reports_root}）\n{_tail(log_path)}",
            "run_dir": str(log_path),
            "duration_s": duration,
        }

    decision_path = report_dir / "5_portfolio" / "decision.md"
    if not decision_path.exists():
        decision_path = report_dir / "decision.md"
    parsed = parse_decision(decision_path.read_text(encoding="utf-8")) if decision_path.exists() else {}
    complete_path = report_dir / "complete_report.md"
    full_report = complete_path.read_text(encoding="utf-8") if complete_path.exists() else ""

    return {
        "status": "done",
        "rating": parsed.get("rating"),
        "summary": parsed.get("summary"),
        "target_price": parsed.get("target_price"),
        "horizon": parsed.get("horizon"),
        "full_report": full_report,
        "run_dir": str(report_dir),
        "duration_s": duration,
    }


def _tail(path: Path, limit: int = 2000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:]


def claim_job() -> dict | None:
    status, body = _post("/internal/analysis/claim")
    if status == 204:
        return None
    if status != 200:
        print(f"[worker] claim failed: {status} {body[:200]!r}", flush=True)
        return None
    return json.loads(body)


def main() -> None:
    if not TOKEN:
        print("[worker] NOPLAN_WORKER_TOKEN 未配置，退出", flush=True)
        sys.exit(2)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: _STOP.set())

    threading.Thread(target=heartbeat_loop, daemon=True).start()
    print(f"[worker] api={API} repo={TA_REPO} data={TA_DATA} timeout={TA_TIMEOUT_MIN}m", flush=True)

    while not _STOP.is_set():
        try:
            job = claim_job()
        except Exception as exc:  # noqa: BLE001
            print(f"[worker] claim error: {exc}", flush=True)
            job = None
        if job:
            result = run_analysis(job)
            status, body = _post(f"/internal/analysis/{job['id']}/finish", result, timeout=60)
            print(f"[worker] finish job={job['id']} -> {status} {body[:120]!r}", flush=True)
        else:
            _STOP.wait(POLL_SEC)


if __name__ == "__main__":
    main()