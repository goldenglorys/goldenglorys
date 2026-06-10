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

LANGUAGE_BY_EXT = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".jsx": "JavaScript", ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".java": "Java",
    ".c": "C", ".h": "C", ".cpp": "C++", ".hpp": "C++", ".cs": "C#", ".php": "PHP",
    ".swift": "Swift", ".kt": "Kotlin", ".sh": "Shell", ".sql": "SQL",
    ".html": "HTML", ".css": "CSS", ".scss": "SCSS", ".svelte": "Svelte",
    ".vue": "Vue", ".md": "Markdown", ".json": "JSON", ".yml": "YAML", ".yaml": "YAML",
    ".jl": "Julia", ".r": "R", ".dart": "Dart", ".scala": "Scala", ".lua": "Lua",
    ".ex": "Elixir", ".exs": "Elixir",
}

NOISE_VERBS = {"merge", "revert"}


def load_json(path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return default
    return default


def fmt_number(n):
    n = int(n)
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


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
            file_touch_counter[filepath] += 1
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
            lang = LANGUAGE_BY_EXT.get(ext)
            if lang:
                recent_lang_counter[lang] += 1

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

    weekend_count = sum(1 for ts in timestamps if ts.weekday() >= 5)
    weekend_pct = round(100 * weekend_count / len(timestamps))

    after_sunset = sum(1 for ts in timestamps if ts.hour >= 18 or ts.hour < 6)
    after_sunset_pct = round(100 * after_sunset / len(timestamps))

    gaps = [
        (timestamps[i] - timestamps[i - 1]).total_seconds()
        for i in range(1, len(timestamps))
    ]
    avg_gap_hours = (sum(gaps) / len(gaps) / 3600) if gaps else 0
    longest_gap_hours = (max(gaps) / 3600) if gaps else 0

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
    return [verb for verb, _ in counter.most_common(3)]


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
    return "█" * filled + "░" * (width - filled)


def count_total_repos():
    total = 0
    for visibility in ("public", "private"):
        base = REPOS_DIR / visibility
        if base.exists():
            total += sum(1 for p in base.iterdir() if (p / ".git").exists())
    return total


def build_block():
    cloc_public = cloc_total(load_json(REPO_ROOT / "cloc-public.json"))
    cloc_private = cloc_total(load_json(REPO_ROOT / "cloc-private.json"))
    total_loc = cloc_public + cloc_private

    public_pct = round(100 * cloc_public / total_loc) if total_loc else 0
    private_pct = (100 - public_pct) if total_loc else 0

    churn_week = {"additions": 0, "deletions": 0}
    for visibility in ("public", "private"):
        data = load_json(REPO_ROOT / f"churn-{visibility}-week.json", {}) or {}
        churn_week["additions"] += data.get("additions", 0)
        churn_week["deletions"] += data.get("deletions", 0)

    commit_data = collect_commit_data()
    temporal = compute_temporal_stats(commit_data["timestamps"])
    verbs = compute_commit_verbs(commit_data["subjects"])

    files_per_commit = commit_data["files_per_commit"]
    avg_files = (sum(files_per_commit) / len(files_per_commit)) if files_per_commit else 0
    p95_files = percentile(files_per_commit, 95)

    most_touched = commit_data["file_touch_counter"].most_common(1)
    most_touched_file = most_touched[0][0] if most_touched else "n/a"

    top_languages = commit_data["recent_lang_counter"].most_common(8)
    max_lang_count = top_languages[0][1] if top_languages else 0

    active_repos = len(commit_data["active_repos"])
    total_repos = count_total_repos()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    lines = []
    lines.append(f"{today} · telemetry")
    lines.append("")
    if total_loc:
        lines.append(f"code      {fmt_number(total_loc)} loc  ({public_pct}% public / {private_pct}% private)")
    else:
        lines.append("code      no data yet")
    lines.append(
        f"this week +{fmt_number(churn_week['additions'])} / -{fmt_number(churn_week['deletions'])}"
    )
    lines.append("")

    if temporal:
        lines.append(f"peak hour      {temporal['peak_hour']:02d}:00 UTC")
        lines.append(f"after sunset   {temporal['after_sunset_pct']}%")
        lines.append(f"weekend work   {temporal['weekend_pct']}%")
        lines.append(f"streak         {temporal['current_streak']}d (longest {temporal['longest_streak']}d)")
        if temporal["avg_gap_hours"]:
            lines.append(
                f"avg gap        {temporal['avg_gap_hours']:.1f}h (longest {temporal['longest_gap_hours']:.0f}h)"
            )
        lines.append("")

    lines.append(f"files/commit   {avg_files:.1f} avg, {p95_files:.0f} p95")
    lines.append(f"most touched   {most_touched_file}")
    if verbs:
        lines.append(f"top verbs      {', '.join(verbs)}")
    lines.append("")
    lines.append(f"repos          {active_repos} active / {total_repos} total")

    if top_languages:
        lines.append("")
        lines.append("languages (90d, by commits)")
        for lang, count in top_languages:
            lines.append(f"  {lang:<12} {bar(count, max_lang_count)} {count}")

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
