"""Google Maps scraping engine wrapper.

The actual Maps pull is delegated to an external CLI scraper (gosom by
default) rather than reimplemented here. That choice is deliberate: Maps
scraping is an arms race that a general-purpose pipeline will lose, and the
part worth owning is everything that happens to the data afterwards.

The one hard requirement on the scraper is that it emits the FULL category
array per listing, not just the primary. Roughly speaking, the classifier's
deny stage is checking ten labels per business; with only the primary it is
checking one, and the disqualifying label is very often not the first.

Blocking is detected by watching the log stream, because Google soft-bans by
degrading rather than by returning an error. A run that quietly starts
returning three results per query instead of twenty looks like a bad keyword
unless something is counting.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

# Log substrings that mean Google is pushing back rather than that the query
# was bad.
BLOCK_MARKERS = (
    "consent", "captcha", "unusual traffic", "/sorry/", "rate limit", "429",
)


class ScrapeEngine:
    def __init__(self, cfg, data_dir: Path) -> None:
        s = cfg.get("scrape") or {}
        self.mode = s.get("engine", "auto")
        self.native = Path(str(s.get("native_binary") or "")).expanduser()
        self.image = s.get("docker_image") or "gosom/google-maps-scraper"
        self.depth = int(s.get("depth", 10))
        self.concurrency = int(s.get("concurrency", 4))
        self.inactivity = s.get("inactivity", "2m")
        self.language = s.get("language", "en")
        self.proxies = os.environ.get(s.get("proxies_env") or "LEADPIPE_PROXIES") or None
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def resolve(self) -> str:
        """'native' or 'docker'. `auto` prefers the native binary."""
        if self.mode in ("native", "docker"):
            return self.mode
        if self.native and self.native.exists():
            return "native"
        return "docker"

    def available(self) -> tuple:
        """(ok, message). Checked before a run so a missing scraper fails in
        one second rather than after the queue has been built."""
        engine = self.resolve()
        if engine == "native":
            if self.native and self.native.exists():
                return True, "native binary at %s" % self.native
            return False, (
                "scrape.engine is 'native' but no binary at %s. See SETUP.md."
                % self.native
            )
        try:
            proc = subprocess.run(
                ["docker", "image", "inspect", self.image],
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as e:
            return False, "docker is not usable: %s" % e
        if proc.returncode != 0:
            return False, (
                "docker image %s is not present. Run: docker pull %s"
                % (self.image, self.image)
            )
        return True, "docker image %s" % self.image

    def run(self, queries, out_name: str, concurrency: int | None = None,
            inactivity: str | None = None) -> dict:
        """Run one batch. Returns {ok, path, rows, failure_rate, ...}."""
        stem = re.sub(r"\.(csv|json)$", "", out_name)
        in_path = self.data_dir / (stem + ".queries.txt")
        out_path = self.data_dir / out_name
        in_path.write_text("\n".join(queries) + "\n", encoding="utf-8")
        if out_path.exists():
            out_path.unlink()

        conc = concurrency or self.concurrency
        engine = self.resolve()
        if engine == "native":
            cmd = [str(self.native), "-input", str(in_path), "-results", str(out_path)]
        else:
            cmd = [
                "docker", "run", "--rm", "-v", "%s:/data" % self.data_dir, self.image,
                "-input", "/data/%s" % in_path.name,
                "-results", "/data/%s" % out_path.name,
            ]
        cmd += [
            "-depth", str(self.depth),
            "-c", str(conc),
            "-exit-on-inactivity", inactivity or self.inactivity,
            "-lang", self.language,
            "-json",
        ]
        if self.proxies:
            cmd += ["-proxies", self.proxies]

        # Generous ceiling so one hung query cannot wedge an overnight run.
        timeout = 120 + len(queries) * 90
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            log = (proc.stderr or "") + (proc.stdout or "")
            ok = proc.returncode == 0
        except subprocess.TimeoutExpired as e:
            raw = e.stderr or b""
            log = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
            ok = False
        except OSError as e:
            return {"ok": False, "path": str(out_path), "rows": 0,
                    "jobs_finished": 0, "jobs_failed": 1, "blocked_signals": 0,
                    "failure_rate": 1.0, "error": str(e)}

        stats = analyze_log(log)
        rows = count_records(out_path) if out_path.exists() else 0
        return {"ok": ok, "path": str(out_path), "rows": rows, **stats}


def analyze_log(log: str) -> dict:
    """Turn a scraper log into a failure rate.

    The failure rate is what the coordinator's block tree reads, so it counts
    both explicit failures and block markers — a run that is being throttled
    reports success while returning nothing.
    """
    finished = log.count('"message":"job finished"')
    failed = log.count('"status":"failed"') + log.count('"level":"error"')
    lower = log.lower()
    blocked = sum(lower.count(m) for m in BLOCK_MARKERS)
    total = max(finished + failed, 1)
    return {
        "jobs_finished": finished,
        "jobs_failed": failed,
        "blocked_signals": blocked,
        "failure_rate": round((failed + blocked) / total, 3),
    }


def count_records(path) -> int:
    """Record count of a scraper output file (JSON array or NDJSON)."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return 0
    if not text:
        return 0
    if text.startswith("["):
        try:
            return len(json.loads(text))
        except json.JSONDecodeError:
            return 0
    return sum(1 for line in text.splitlines() if line.strip().startswith("{"))
