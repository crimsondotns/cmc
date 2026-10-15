"""Logging แบบ Progress bar + สรุปท้ายรอบ (ใช้ร่วมกันระหว่าง main.py และ dex.py)

- ระหว่างรัน: แสดง progress bar ของแต่ละขั้นตอน + log เฉพาะ warning/error
- จบรอบ: ตารางสรุป ok/failed ต่อขั้นตอน และเวลาที่ใช้
- ไม่ใช่ terminal (เช่น GitHub Actions): ไม่แสดง bar แต่พิมพ์ 1 บรรทัดตอนจบแต่ละขั้นตอนแทน
"""
import threading
import time
from contextlib import contextmanager

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, ProgressColumn, TaskProgressColumn, TextColumn
from rich.table import Table

console = Console(highlight=False, soft_wrap=True)


def format_thai_duration(seconds):
    """1106 -> '18 นาที 26 วินาที', 3900 -> '1 ชั่วโมง 5 นาที'"""
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} ชั่วโมง {minutes} นาที"
    if minutes:
        return f"{minutes} นาที {secs} วินาที"
    return f"{secs} วินาที"


class ThaiTimeRemainingColumn(ProgressColumn):
    def render(self, task):
        if task.finished:
            return "เสร็จแล้ว"
        if task.time_remaining is None:
            return "กำลังคำนวณเวลา…"
        return f"เหลืออีก {format_thai_duration(task.time_remaining)}"


class Report:
    def __init__(self):
        self._lock = threading.Lock()
        self._start = time.monotonic()
        self.counts = {}      # stage -> {"ok": int, "failed": int}
        self.warnings = 0
        self._advance = None

    # ---------- counters ----------
    def _bucket(self, stage):
        return self.counts.setdefault(stage, {"ok": 0, "failed": 0})

    def ok(self, stage):
        with self._lock:
            self._bucket(stage)["ok"] += 1

    def fail(self, stage, label, reason):
        with self._lock:
            self._bucket(stage)["failed"] += 1
        console.print(f"[red]ERROR[/] {label}: {reason}")

    def warn(self, message):
        with self._lock:
            self.warnings += 1
        console.print(f"[yellow]WARN [/] {message}")

    def info(self, message):
        console.print(f"[dim]{message}[/]")

    @property
    def failed_total(self):
        return sum(c["failed"] for c in self.counts.values())

    # ---------- progress ----------
    @contextmanager
    def progress(self, label, total):
        """with report.progress("Fetching", n) as advance: ... advance("bitcoin")

        label ที่ส่งให้ advance() จะแสดงต่อท้าย bar (ชื่อของรายการที่เพิ่งเสร็จ)
        """
        started = time.monotonic()
        if not console.is_terminal:
            yield lambda current="": None
            console.print(f"{label}: {total} items in {time.monotonic() - started:.1f}s")
            return

        columns = (TextColumn(f"{label:<10}"), BarColumn(), MofNCompleteColumn(),
                   TaskProgressColumn(), ThaiTimeRemainingColumn(),
                   TextColumn("[cyan]{task.fields[current]}"))
        with Progress(*columns, console=console, transient=False) as bar:
            task = bar.add_task(label, total=total, current="")
            yield lambda current="": bar.update(task, advance=1, current=current)

    # ---------- summary ----------
    def summary(self, title):
        elapsed = time.monotonic() - self._start
        table = Table(title=title, title_justify="left")
        table.add_column("ขั้นตอน")
        table.add_column("ok", justify="right", style="green")
        table.add_column("failed", justify="right", style="red")
        for stage, c in self.counts.items():
            table.add_row(stage, str(c["ok"]), str(c["failed"]))
        console.print()
        console.print(table)

        status = "[green]✅ สำเร็จ[/]" if self.failed_total == 0 else f"[yellow]⚠ failed {self.failed_total}[/]"
        console.print(f"{status} · warnings {self.warnings} · {elapsed:.1f}s")


report = Report()
