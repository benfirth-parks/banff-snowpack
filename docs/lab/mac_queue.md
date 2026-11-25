# The Mac queue: heavy jobs the cloud runs on the owner's Mac (ADR-148)

Research and decision support only, not an avalanche forecast.

Light work runs in the cloud: the daily update, the stability check, the cloud training queue of the lab's own
models (`scripts/cloud_queue.sh`, ADR-130), analysis, decision records and pull requests. The jobs that need
SNOWPACK runs at scale or many hours (training with SNOWPACK agents, the promotion check `check-loso`, winter
tests, group checks, large competitions) run on the owner's Mac. The cloud queues them, and the Mac runs them
without anyone carrying files between the two.

```
cloud scheduled task ──writes──▶ mac-queue branch: queue/<id>.json
                                          │  every 5 min (launchd)
                                          ▼
                    Mac: snowagent lab mac-queue tick ──▶ lab job runner (shows in the lab app)
                                          │  when the job ends
                                          ▼
cloud scheduled task ◀──reads── mac-results branch: results/<id>/  (+ heartbeat.json once a day)
```

## Install on the Mac (once)

From the repository root in Terminal, on `main` with the lab set up (`run_locally.md` steps 0 to 5):

```bash
git pull && bash scripts/mac_runner/install.sh
```

It checks that the Mac can push to GitHub. If it can't, it prints the two commands that fix that (`gh auth login`,
then `gh auth setup-git`). Until then, results wait in `data/lab/outputs/mac_queue/outbox/` and go out at the next
tick that can push. To remove it: `bash scripts/mac_runner/uninstall.sh`.

## How the Mac is spared

- One job at a time, and none while any other lab job runs, including one started in the lab app.
- `nice -n 10`, so the Mac stays responsive while a job runs.
- `--workers` is the number of performance cores minus two. To change it, run the install again with
  `SNOWAGENT_MAC_WORKERS=4 bash scripts/mac_runner/install.sh`.
- `caffeinate -i -s` keeps the Mac awake while a job runs.
- Jobs start and resume on battery as well as on mains power; the owner manages the power (ADR-151).
- A job that was cut (sleep, reboot, the lid closed) resumes at the next tick, up to 3 times.
  Training continues with `--resume`, and the other jobs continue under the same id.
- Before a new job, `main` is fast-forwarded, if the checkout is on a clean `main`. Otherwise the job runs the code
  that is there, and the runner log says so.

## Disk guard (ADR-158)

On 2026-11-24 the Mac's disk filled to under 1 GB, and a reboot freed about 255 GB; the cause is not yet known
(ADR-159). The runner now:

- gives every job its own temporary folder, `data/lab/outputs/mac_queue/tmp` (its `TMPDIR`), and clears it after
  every job and whenever nothing runs, together with any SNOWPACK work folder (`sp_*`) older than two hours left in
  the system's temporary folder;
- starts or resumes nothing with less than **50 GB** free (`SNOWAGENT_MAC_MIN_FREE_GB`);
- stops a running job below **20 GB** free (`SNOWAGENT_MAC_STOP_FREE_GB`), signalling its whole process group so the
  workers stop too, and resumes it once 50 GB are free again (that stop does not count as one of the 3 attempts);
- clears the training cache (`data/lab/outputs/cache`, rebuilt as needed) when it passes **30 GB**
  (`SNOWAGENT_MAC_CACHE_MAX_GB`, checked every 6 hours) or the disk
  is low; clearing renames the folder aside and deletes it in the background, so it never holds up a check (ADR-160);
- checks every 5 minutes instead of every 30, so the guard acts quickly; the heartbeat reports the free space;
- writes a disk report when free space falls 5 GB or more between checks, or is below 50 GB (hourly at most):
  the biggest folders, temporary folders, swap and files deleted but still open, in `outputs/mac_queue/disk/` and
  on `mac-results` under `disk/` (ADR-159).

## A queue entry

One file per job, `queue/<id>.json` on the `mac-queue` branch:

```json
{"id": "night-2026-11-24-r6-p8", "kind": "train",
 "args": ["--rounds", "6", "--population", "8", "--screen-cases", "30", "--seed", "0"],
 "note": "why this run, in a sentence", "created_at": "2026-11-23T20:00:00Z"}
```

- `kind` is one of `train`, `compete`, `check-loso`, `winter-test`, `group-check`, `stability-check`,
  `sensitivity`, `rescore` or `build-cases`. Nothing else can be queued.
- `args` are the command's own options as a list. The runner adds `--data-root`, `--config` and `--workers`, and
  refuses an entry that sets them.
- The run or check is named after `id` (`--run-id` or `--check-id`) unless `args` name it.
- To cancel, rewrite the file with `"cancel": true`. A running job then gets the lab's Stop.
- Check entries before pushing them: `snowagent lab mac-queue check queue/*.json`.

Entries run in `created_at` order. An entry the runner has seen is never run twice, and its state is kept in
`data/lab/outputs/mac_queue/state.json`.

## Seeing the queue

- In the lab app, under **Background › Mac queue**. It shows what's running, what's waiting next and what has
  ended, and whether each job's results reached the cloud. Its **Check the queue now** button runs a check
  straight away instead of waiting for the half-hourly one.
- On GitHub: issue #104, where the cloud says what it queued and why, plus the `mac-queue` and `mac-results`
  branches.
- In Terminal: `snowagent lab mac-queue status`.

When the Mac runs out of work, the next tick sends `heartbeat.json` with `"idle": true` and `idle_since` straight
away, without waiting for the daily heartbeat. The cloud task then messages the owner and refills the queue (ADR-150).

## What comes back

`results/<id>/` on `mac-results`:

- `result.json`: the state (finished, failed, stopped or rejected), the exit code, the lab job, the Mac's code
  version and the workers, plus any output file too big to publish
- `log_tail.txt`: the last 60 kB of the job's log
- `outputs/<kind folder>/<run or check id>/...`: the run's `.json`, `.jsonl`, `.csv`, `.md`, `.txt`, `.log` and
  `.yaml` files, each up to 2 MB and 25 MB in all. The cache is never published.

`heartbeat.json` at the branch root says, once a day, that the runner is alive, along with what it is doing.

On the Mac, `snowagent lab mac-queue status` shows the same, and `data/lab/outputs/mac_queue/runner.log` has every
tick's decisions.
