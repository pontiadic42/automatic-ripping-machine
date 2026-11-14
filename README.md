# A2RM: Automatic Automatic Ripping Machine

**A2RM** (say "A-squared-RM") is a fork of the
[Automatic Ripping Machine (ARM)](https://github.com/automatic-ripping-machine/automatic-ripping-machine).
ARM already rips a disc when you put it in. A2RM tries to take the remaining manual steps out: working out what the
disc is, which titles on it to keep, what to name the files, and cleaning up afterwards. The idea is that you put a
disc in and only hear about it when it really needs you.

A2RM is based on ARM 2.24.4. All the credit for ARM itself goes to its authors and contributors; see
[Credits](#credits). A2RM is a personal fork and isn't affiliated with or supported by the ARM project, so please
don't report A2RM problems to them.

## What A2RM adds to ARM

### Identification
- **Confidence score (0-100)** for every disc, with plain-language reasons: how well the disc label matches the
  title, whether the main title's length matches TMDb's runtime, and whether the same disc was ripped before.
- **No waiting when it's sure.** At or above a score you choose (default 90) the rip starts right away instead of
  sitting through the manual-wait period. Below another score (default 70) A2RM waits longer and asks you.
- **Runtime matching.** When a label is vague, A2RM searches TMDb and picks the result whose runtime matches the
  disc's main title.
- **Remembers discs.** A disc without a label that was identified before (same DVD fingerprint) gets its earlier
  title automatically.
- **Phone notifications through [ntfy](https://ntfy.sh)** with "Yes, that's it" and "Pick the title" buttons.

### Ripping the right titles
- **Title analysis** from MakeMKV's title information (segment maps, languages, audio and subtitle streams). Each
  title is classed as the main feature, another cut (theatrical / extended), a language version, a duplicate (copy
  protection), a "play all" title, an extra, or a TV episode.
- Only what's needed is ripped. Duplicates and other-language versions are skipped (configurable). When language
  versions can't be told apart, all are kept and the job page shows still frames from each.
- **Jellyfin-friendly names:** `Movie (Year).mkv`, `Movie (Year) - Extended cut (+11 min).mkv` (Jellyfin offers it as a
  version), readable names in `extras/`, and `Show/Season 01/Show S01E03.mkv` matched from TMDb's episode runtimes.

### Checks and safety nets
- **Already ripped?** Rip again, rip and tell you, or skip and eject.
- **Disk space** is checked before ripping.
- **Finished files are checked** against their disc title's length before the raw rip is deleted; a file that looks
  cut short keeps its raw files and you're told.
- **MakeMKV retries** after a read failure, with a "clean the disc or try the other drive" hint, and a clear message
  when MakeMKV itself has expired.
- **Stuck-job watchdog:** a rip with no progress for a while is stopped, the disc ejected, and you're told.
- **Correcting a title later moves the files** to the new name (and from `unidentified` to `movies` if needed).
- Optional: keep only your audio/subtitle languages when transcoding.

### Housekeeping
- Daily database backup, cleanup of leftover raw/transcode folders and of failed jobs that were later ripped
  successfully, and a daily summary notification.
- **Jellyfin:** scans the new folder right away, then checks it matched the same movie or show.

### Web UI
- New layout: a home page with one "bay" per drive (empty / identifying / waiting for you / ripping, with steps,
  progress and time left), a "needs you" strip with buttons (accept title, pick another, cancel job, delete files,
  dismiss), and recently finished rips.
- A poster **Library** with filters (movies, TV, music, unidentified, failed) and search.
- Works on phones (bottom tab bar, list layouts), light and dark themes.
- **Settings > Automation** page for every A2RM option.

### Fixes to ARM itself
A2RM also carries a set of bug fixes to ARM 2.24.4: wrong titles from disc labels, crashes in identification,
notifications for empty drives, stale drive locks, abandoned jobs that kept MakeMKV running, config file rewrites,
secrets in logs, database locks while ripping, double runs for one disc, slow drive spin-up, and more. Most of these
were also offered to ARM as pull requests.

## Configuration

Everything A2RM adds is a normal setting in `arm.yaml` (added automatically on first start) and can be changed on
**Settings > Automation** in the web UI. Each feature can be switched off. Things you'll want to set:

| Setting | What it's for |
|---|---|
| `NTFY_SERVER`, `NTFY_TOPIC` (+ `NTFY_TOKEN`) | Phone notifications. Empty = off. |
| `ARM_PHONE_URL` | How your phone reaches A2RM (for notification buttons), e.g. `http://192.168.1.50:8080`. |
| `JELLYFIN_URL`, `JELLYFIN_API_KEY` | Jellyfin scans and match checks. Empty = off. |
| `CONFIDENCE_SKIP_WAIT_ABOVE`, `CONFIDENCE_NOTIFY_BELOW` | When to start right away / when to ask you. |
| `ALREADY_RIPPED_ACTION` | `rip`, `notify` or `skip` for discs you already have. |
| `PREFERRED_LANGUAGE` | Picks the main version on multi-language discs (ISO 639-2, e.g. `eng`). |

The housekeeping tasks (watchdog, backups, cleanup, summary, Jellyfin check) run from a small scheduled command,
for example every 5 minutes from the Docker host's cron:

```bash
docker exec -u arm -w /opt/arm -e ARM_LOCAL_TZ=$(timedatectl show -p Timezone --value) arm-rippers python3 -m arm.automation.maintenance tick
```

## Install

A2RM is installed like ARM; see ARM's [wiki](https://github.com/automatic-ripping-machine/automatic-ripping-machine/wiki)
and [Docker guide](https://github.com/automatic-ripping-machine/automatic-ripping-machine/wiki/docker) for the
basics (drives, volumes, permissions). The `Dockerfile` here is ARM's, so building this repo gives you an A2RM image.
A2RM was developed and is run by layering its files onto the ARM 2.24.4 Docker image; a from-scratch build of this
repository hasn't been tested yet.

Some things to know:
- **MakeMKV** isn't part of A2RM. ARM's installer downloads it from makemkv.com. The free beta stops working with
  each new MakeMKV release, so rebuild with the current version when it does (or buy a key).
- Keep the database, logs and MakeMKV settings on host volumes so a container rebuild doesn't lose them.

## Tests

```bash
python3 -m pytest test/unittest
```

The `test_automation_*` files cover the A2RM features. Run them inside the ARM image (they need its tools and Python
packages), with `setup/arm.yaml` copied to `/etc/arm/config/arm.yaml`.

## Credits

A2RM is built on the [Automatic Ripping Machine](https://github.com/automatic-ripping-machine/automatic-ripping-machine),
created by Benjamin Bryan and maintained by Andrew Sneed and many contributors. Thank you for ARM.
The original write-up: https://b3n.org/automatic-ripping-machine

A2RM changes were written with the help of [Claude Code](https://claude.com/claude-code).

## License

[MIT License](LICENSE), the same as ARM. The original copyright notices are kept.
