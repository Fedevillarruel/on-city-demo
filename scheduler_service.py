import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from cron_store import add_run_log, list_jobs, mark_job_run, seed_default_jobs, update_run_log
from intelligence_store import process_post_run

BASE_DIR = Path(__file__).resolve().parent


def _now() -> datetime:
    return datetime.now()


def _build_command(job: dict) -> list[str]:
    cmd = [sys.executable, str(BASE_DIR / "main.py")]
    if int(job.get("run_oncity", 0)):
        cmd.append("--oncity")
    if int(job.get("run_fravega", 0)):
        cmd.append("--fravega")
    if int(job.get("run_cetrogar", 0)):
        cmd.append("--cetrogar")
    return cmd


def _should_run(job: dict, now: datetime) -> bool:
    if not int(job.get("enabled", 0)):
        return False

    if int(job.get("hour", -1)) != now.hour or int(job.get("minute", -1)) != now.minute:
        return False

    last_run = job.get("last_run")
    if not last_run:
        return True

    try:
        dt = datetime.fromisoformat(last_run)
        # Avoid duplicate runs in the same minute.
        return dt.strftime("%Y-%m-%d %H:%M") != now.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return True


def _run_job(job: dict) -> None:
    now = _now()
    run_started = now.isoformat(timespec="seconds")
    command = _build_command(job)

    if len(command) <= 2:
        return

    log_id = add_run_log(
        job_id=int(job["id"]),
        status="running",
        message=" ".join(command),
        started_at=run_started,
    )

    print(f"[{run_started}] Ejecutando job #{job['id']} ({job['name']}): {' '.join(command)}")

    try:
        proc = subprocess.run(
            command,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            check=False,
        )
        run_finished = _now().isoformat(timespec="seconds")
        status = "ok" if proc.returncode == 0 else "error"

        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        output = output.strip()

        if status == "ok":
            try:
                post_info = process_post_run(BASE_DIR, run_finished)
                output += (
                    "\n\n[Intelligence] "
                    f"snapshots={post_info.get('snapshots', 0)} "
                    f"alerts={post_info.get('alerts', 0)} "
                    f"report={post_info.get('report', '')}"
                )
            except Exception as exc:
                output += f"\n\n[Intelligence] error post-run: {exc}"

        if len(output) > 5000:
            output = output[:5000] + "\n...[truncated]"

        update_run_log(log_id, status, output or "Sin salida", run_finished)
        mark_job_run(int(job["id"]), run_finished)
        print(f"[{run_finished}] Job #{job['id']} finalizado con estado {status}")
    except Exception as exc:
        run_finished = _now().isoformat(timespec="seconds")
        update_run_log(log_id, "error", f"Excepcion ejecutando job: {exc}", run_finished)
        mark_job_run(int(job["id"]), run_finished)
        print(f"[{run_finished}] Error en job #{job['id']}: {exc}")


def run_scheduler_loop(sleep_seconds: int = 20) -> None:
    seed_default_jobs()
    print("Scheduler iniciado. Revisando jobs habilitados...")
    while True:
        now = _now()
        jobs = list_jobs()
        for job in jobs:
            if _should_run(job, now):
                _run_job(job)
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    run_scheduler_loop()
