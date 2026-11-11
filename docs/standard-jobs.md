# Standard scheduled jobs

Every project runs these five jobs. Use these exact names, so the Schedules tab can find a missing job.

| Job | When (local time) | What it is for |
|---|---|---|
| `check-in` | Every 30 minutes, staggered | Reads the inbox, answers requests, and posts status. |
| `usage-guard` | Every 30 minutes, staggered | Reads weekly usage. Winds work down at the install's limit, stops at its stop limit and tells the owner. The limits are the `usage` keys of `config.json`. |
| `daily-docs-refresh` | Daily, 09:05 to 10:05, staggered | Corrects the README and docs that the last 24 hours of changes made out of date. |
| `daily-retro` | Daily, 12:25 to 12:55, staggered | Posts the retro with the backlog vote, the MOVE votes, and the drift list. |
| `monday-review` | Monday, 10:05 to 11:05, staggered | Finds stale or missing rules in AGENTS.md and CLAUDE.md and proposes changes. |

Each project gets its own even share of each window, so jobs do not start together. Use the times that the command prints.

Claude Code crons can fire up to 30 minutes late. The latest retro job therefore fires by 13:25.

To change the times of jobs that a project already runs, add `--all` to the command. It prints all five jobs. Replace each job, then run its `schedule set` line.

## Add the jobs to a project

1. Run `python3 <crosstalk>/scripts/relay.py schedule standard --from <id>`.
2. Create each job that the command prints. The command gives the harness's own form and spreads the minutes by project.
3. Run each printed `schedule set` line.
4. Open the Schedules tab and check that the project is not under Missing jobs.

With a data root (`relay.py --data-root <dir> schedule standard`, or `CROSSTALK_DATA`), each printed command also carries `--data-root <dir>`.

## Harness notes

- **Claude Code:** `CronCreate` jobs end with the session and expire after 7 days. Create them and report them again at each session start.
- **Hermes:** `hermes cron create` jobs stay until you remove them.

## Other jobs

A project can run more jobs. Report each one with `--about`. The Schedules tab counts a job named `combined-retro` as that participant's `daily-retro`.

The job list lives in `STANDARD_JOBS` in `scripts/crosstalk/schedules.py`. `index.html` repeats the names, and a test keeps them equal.
