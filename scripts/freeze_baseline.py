"""Tag a tested pre-sprint commit using an explicitly supplied administrative boundary."""
import argparse
from datetime import datetime, timezone
import os
import subprocess
import sys


def git(*arguments):
    return subprocess.check_output(["git", *arguments], text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-start", required=True)
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    start = datetime.fromisoformat(args.event_start)
    if start.tzinfo is None:
        raise SystemExit("Event start needs an explicit timezone")
    if git("status", "--porcelain"):
        raise SystemExit("Working tree is not clean")
    if datetime.fromisoformat(git("show", "-s", "--format=%cI", "HEAD")) > start:
        raise SystemExit("HEAD postdates the start; select the validated pre-sprint commit first")
    env = {**os.environ, "EIG_REQUIRE_INSPECT": "1"}
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], check=True, env=env)
    subprocess.run(["git", "tag", "-a", args.tag, "-m",
                    f"Validated pre-sprint baseline; owner-selected administrative boundary {start.isoformat()} (not an official organizer start time); HEAD {git('rev-parse', 'HEAD')}. Full unittest suite passed with pinned Inspect required, no paid model calls. Completed scope: docs/pre-sprint-scope.md; limitations: docs/validation.md."], check=True)
    print(f"Created local annotated tag {args.tag}; push explicitly after review.")


if __name__ == "__main__":
    main()
