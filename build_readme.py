#!/usr/bin/env python3
"""Generate the telemetry block for README.md from cloc/churn JSON and cloned repo history."""
import json
import os
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
REPOS_DIR = REPO_ROOT / "repos"
README_PATH = REPO_ROOT / "README.md"

START_MARKER = "<!-- TELEMETRY START -->"
END_MARKER = "<!-- TELEMETRY END -->"

EXT_LABELS = {
    ".py": "py", ".js": "js", ".ts": "ts", ".tsx": "tsx", ".jsx": "jsx",
    ".go": "go", ".rs": "rs", ".rb": "rb", ".java": "java",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cs": "cs", ".php": "php",
    ".swift": "swift", ".kt": "kt", ".sh": "shell", ".sql": "sql",
    ".html": "html", ".css": "css", ".scss": "scss", ".svelte": "svelte",
    ".vue": "vue", ".md": "md", ".json": "json", ".yml": "yaml", ".yaml": "yaml",
    ".jl": "jl", ".r": "r", ".dart": "dart", ".scala": "scala", ".lua": "lua",
    ".ex": "ex", ".exs": "ex",
}

NOISE_VERBS = {"merge", "revert"}

AFTER_SUNSET_START_HOUR = 20
AFTER_SUNSET_END_HOUR = 6


def load_json(path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return default
    return default


def fmt_comma(n):
    return f"{int(n):,}"


def fmt_compact(n):
    n = int(n)
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def fmt_duration(hours):
    total_minutes = round(hours * 60)
    days, rem = divmod(total_minutes, 24 * 60)
    hrs, _ = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hrs}h"
    return f"{hrs}h"


def cloc_total(cloc_data):
    if not cloc_data:
        return 0
    return int(cloc_data.get("SUM", {}).get("code", 0))


def iter_repos():
    """Yield (visibility, repo_path) for every cloned repo."""
    for visibility in ("public", "private"):
        base = REPOS_DIR / visibility
        if not base.exists():
            continue
        for repo_path in sorted(base.iterdir()):
            if (repo_path / ".git").exists():
                yield visibility, repo_path


def author_args():
    pattern = os.environ.get("AUTHOR_FILTER")
    return ["--author", pattern] if pattern else []


def git_log(repo_path, args):
    result = subprocess.run(
        ["git", "-C", str(repo_path), "log"] + args,
        capture_output=True, text=True, check=False,
    )
    return result.stdout


def collect_commit_data():
    """Walk every cloned repo and collect commit timestamps, subjects, and file changes."""
    timestamps = []
    subjects = []
    files_per_commit = []
    recent_lang_counter = Counter()
    file_touch_counter = Counter()
    active_repos = set()
    now = datetime.now(timezone.utc)

    for visibility, repo_path in iter_repos():
        name = f"{visibility}/{repo_path.name}"

        log_output = git_log(repo_path, ["--pretty=format:%at|%s"] + author_args())
        for line in log_output.splitlines():
            if "|" not in line:
                continue
            ts_str, subject = line.split("|", 1)
            try:
                ts = int(ts_str)
            except ValueError:
                continue
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            timestamps.append(dt)
            subjects.append(subject.strip())
            if (now - dt).days <= 7:
                active_repos.add(name)

        numstat_output = git_log(
            repo_path,
            ["--since=180 days ago", "--numstat", "--pretty=format:__COMMIT__"] + author_args(),
        )
        current_files = 0
        for line in numstat_output.splitlines():
            line = line.strip()
            if not line:
                continue
            if line == "__COMMIT__":
                if current_files:
                    files_per_commit.append(current_files)
                current_files = 0
                continue
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            _, _, filepath = parts
            current_files += 1
            file_touch_counter[f"{repo_path.name}/{filepath}"] += 1
        if current_files:
            files_per_commit.append(current_files)

        recent_files_output = git_log(
            repo_path,
            ["--since=90 days ago", "--name-only", "--pretty=format:"] + author_args(),
        )
        for filepath in recent_files_output.splitlines():
            filepath = filepath.strip()
            if not filepath:
                continue
            ext = Path(filepath).suffix.lower()
            label = EXT_LABELS.get(ext)
            if label:
                recent_lang_counter[label] += 1

    return {
        "timestamps": timestamps,
        "subjects": subjects,
        "files_per_commit": files_per_commit,
        "recent_lang_counter": recent_lang_counter,
        "file_touch_counter": file_touch_counter,
        "active_repos": active_repos,
    }


def compute_temporal_stats(timestamps):
    if not timestamps:
        return {}

    timestamps = sorted(timestamps)
    hour_counter = Counter(ts.hour for ts in timestamps)
    peak_hour, _ = hour_counter.most_common(1)[0]

    weekend_timestamps = [ts for ts in timestamps if ts.weekday() >= 5]
    weekend_pct = round(100 * len(weekend_timestamps) / len(timestamps))

    saturdays = sum(1 for ts in weekend_timestamps if ts.weekday() == 5)
    sundays = len(weekend_timestamps) - saturdays
    if weekend_timestamps:
        sat_pct = round(100 * saturdays / len(weekend_timestamps))
        sun_pct = 100 - sat_pct
    else:
        sat_pct = sun_pct = 0

    after_sunset = sum(
        1 for ts in timestamps
        if ts.hour >= AFTER_SUNSET_START_HOUR or ts.hour < AFTER_SUNSET_END_HOUR
    )
    after_sunset_pct = round(100 * after_sunset / len(timestamps))

    gaps = [
        (timestamps[i] - timestamps[i - 1]).total_seconds() / 3600
        for i in range(1, len(timestamps))
    ]
    avg_gap_hours = (sum(gaps) / len(gaps)) if gaps else 0
    longest_gap_hours = max(gaps) if gaps else 0

    days = sorted({ts.date() for ts in timestamps})
    longest_streak = 1
    streak = 1
    for i in range(1, len(days)):
        if (days[i] - days[i - 1]).days == 1:
            streak += 1
        else:
            longest_streak = max(longest_streak, streak)
            streak = 1
    longest_streak = max(longest_streak, streak)

    today = datetime.now(timezone.utc).date()
    day_set = set(days)
    current_streak = 0
    cursor = today
    while cursor in day_set:
        current_streak += 1
        cursor = cursor.fromordinal(cursor.toordinal() - 1)

    return {
        "peak_hour": peak_hour,
        "weekend_pct": weekend_pct,
        "sat_pct": sat_pct,
        "sun_pct": sun_pct,
        "after_sunset_pct": after_sunset_pct,
        "avg_gap_hours": avg_gap_hours,
        "longest_gap_hours": longest_gap_hours,
        "longest_streak": longest_streak,
        "current_streak": current_streak,
    }


def compute_commit_verbs(subjects):
    counter = Counter()
    for subject in subjects:
        match = re.match(r"[a-zA-Z]+", subject)
        if not match:
            continue
        verb = match.group(0).lower()
        if verb in NOISE_VERBS or len(verb) < 3:
            continue
        counter[verb] += 1
    return counter.most_common(2)


def percentile(values, pct):
    if not values:
        return 0
    values = sorted(values)
    k = (len(values) - 1) * pct / 100
    f = int(k)
    c = min(f + 1, len(values) - 1)
    if f == c:
        return values[f]
    return values[f] + (values[c] - values[f]) * (k - f)


def bar(value, max_value, width=20):
    filled = round(width * value / max_value) if max_value > 0 else 0
    filled = max(0, min(width, filled))
    return "█" * filled + "·" * (width - filled)


def count_total_repos():
    total = 0
    for visibility in ("public", "private"):
        base = REPOS_DIR / visibility
        if base.exists():
            total += sum(1 for p in base.iterdir() if (p / ".git").exists())
    return total


def row(label, left="", right=""):
    return f"  {label:<14}{left:<24}{right}".rstrip()


def build_block():
    cloc_public = cloc_total(load_json(REPO_ROOT / "cloc-public.json"))
    cloc_private = cloc_total(load_json(REPO_ROOT / "cloc-private.json"))
    total_loc = cloc_public + cloc_private

    public_pct = round(100 * cloc_public / total_loc) if total_loc else 0
    private_pct = (100 - public_pct) if total_loc else 0

    churn_week = {"additions": 0, "deletions": 0}
    churn_all = {"additions": 0, "deletions": 0}
    for visibility in ("public", "private"):
        week_data = load_json(REPO_ROOT / f"churn-{visibility}-week.json", {}) or {}
        churn_week["additions"] += week_data.get("additions", 0)
        churn_week["deletions"] += week_data.get("deletions", 0)

        all_data = load_json(REPO_ROOT / f"churn-{visibility}-all.json", {}) or {}
        churn_all["additions"] += all_data.get("additions", 0)
        churn_all["deletions"] += all_data.get("deletions", 0)

    commit_data = collect_commit_data()
    temporal = compute_temporal_stats(commit_data["timestamps"])
    top_verbs = compute_commit_verbs(commit_data["subjects"])

    files_per_commit = commit_data["files_per_commit"]
    avg_files = (sum(files_per_commit) / len(files_per_commit)) if files_per_commit else 0
    p95_files = percentile(files_per_commit, 95)
    max_files = max(files_per_commit) if files_per_commit else 0

    most_touched = commit_data["file_touch_counter"].most_common(1)
    most_touched_str = (
        f"{most_touched[0][0]} ({most_touched[0][1]}×)" if most_touched else "n/a"
    )

    top_languages = commit_data["recent_lang_counter"].most_common(8)
    max_lang_count = top_languages[0][1] if top_languages else 0

    active_repos = len(commit_data["active_repos"])
    total_repos = count_total_repos()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    net = churn_week["additions"] - churn_week["deletions"]

    lines = []
    lines.append(f"{today} · telemetry")
    lines.append("")

    if total_loc:
        lines.append(row("output", f"{fmt_comma(total_loc)} loc", f"public {public_pct}% · private {private_pct}%"))
    else:
        lines.append(row("output", "no data yet"))

    lines.append(row(
        "past 7d",
        f"+{fmt_comma(churn_week['additions'])} / -{fmt_comma(churn_week['deletions'])}",
        f"net {'+' if net >= 0 else ''}{fmt_comma(net)}",
    ))
    lines.append(row("lifetime", f"+{fmt_compact(churn_all['additions'])} / -{fmt_compact(churn_all['deletions'])}"))

    if top_languages:
        lines.append("")
        lines.append("  recent focus (90d, by commits touching that language)")
        for label, count in top_languages:
            lines.append(f"  {label:<8}{bar(count, max_lang_count)}{count:>7}")

    if temporal:
        lines.append("")
        lines.append(row(
            "peak hour",
            f"{temporal['peak_hour']:02d}:00 UTC",
            f"{temporal['after_sunset_pct']}% past sunset (20:00–06:00)",
        ))
        lines.append(row(
            "cadence",
            f"{fmt_duration(temporal['avg_gap_hours'])} avg gap",
            f"longest: {fmt_duration(temporal['longest_gap_hours'])}",
        ))
        lines.append(row(
            "weekend share",
            f"{temporal['weekend_pct']}%",
            f"sat {temporal['sat_pct']}% / sun {temporal['sun_pct']}%",
        ))
        lines.append(row(
            "files/commit",
            f"{avg_files:.1f} avg",
            f"p95 {p95_files:.0f}, max {max_files}",
        ))
        lines.append(row(
            "commit streak",
            f"{temporal['current_streak']} days",
            f"longest ever: {temporal['longest_streak']}",
        ))

    lines.append("")
    lines.append(row("active repos", f"{active_repos} of {total_repos}"))
    lines.append(row("most-touched", most_touched_str))
    if top_verbs:
        verb, _ = top_verbs[0]
        if len(top_verbs) > 1:
            runner_up, _ = top_verbs[1]
            lines.append(row("favorite verb", f'"{verb}"', f'runner up: "{runner_up}"'))
        else:
            lines.append(row("favorite verb", f'"{verb}"'))

    body = "\n".join(lines)
    return f"{START_MARKER}\n```\n{body}\n```\n{END_MARKER}"


def update_readme(block):
    text = README_PATH.read_text()
    if START_MARKER in text and END_MARKER in text:
        pattern = re.compile(re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER), re.DOTALL)
        text = pattern.sub(block, text)
    else:
        text = text.rstrip("\n") + "\n\n" + block + "\n"
    README_PATH.write_text(text)


def main():
    block = build_block()
    update_readme(block)


if __name__ == "__main__":
    main()
