"""
All automation settings in one place: default, type, group and help text.

The same keys are in setup/arm.yaml (so arm.yaml gets them on upgrade) and are shown on
Settings > Automation. Code reads them with ``get(KEY)``, which falls back to the
default here when arm.yaml doesn't have the key yet.
"""
import dataclasses


@dataclasses.dataclass(frozen=True)
class Setting:
    key: str
    default: object
    kind: str            # bool | int | str | secret | choice | time | langs
    group: str
    label: str
    help: str
    choices: tuple = ()


GROUPS = (
    ("notify", "Phone notifications (ntfy)"),
    ("identify", "Identification"),
    ("before", "Before ripping"),
    ("titles", "Titles, cuts and languages"),
    ("checks", "Checks and retries"),
    ("jellyfin", "Jellyfin"),
    ("housekeeping", "Housekeeping"),
)

SCHEMA = (
    # Phone notifications
    Setting("NTFY_SERVER", "", "str", "notify", "ntfy server",
            "Address of your ntfy server, e.g. https://ntfy.example.com. Leave empty to turn phone "
            "notifications off."),
    Setting("NTFY_TOPIC", "", "str", "notify", "ntfy topic", "Topic your phone is subscribed to."),
    Setting("NTFY_TOKEN", "", "secret", "notify", "ntfy access token",
            "Only needed if your ntfy server requires a login (tk_...)."),
    Setting("NTFY_SEND_ALL", False, "bool", "notify", "Send every ARM notification",
            "Also send ARM's normal messages (disc found, rip complete, ...) to ntfy. When off, ntfy only "
            "gets things that need you, problems and the daily summary."),
    Setting("ARM_PHONE_URL", "", "str", "notify", "ARM address for your phone",
            "How your phone reaches this ARM, used for the buttons in notifications, "
            "e.g. http://192.168.1.50:8080. Empty uses UI_BASE_URL."),

    # Identification
    Setting("CONFIDENCE_ENABLED", True, "bool", "identify", "Score identification confidence",
            "Rate how sure ARM is about each disc's title (0-100) from the label, the TMDb runtime and "
            "earlier rips of the same disc."),
    Setting("CONFIDENCE_NOTIFY_BELOW", 70, "int", "identify", "Ask me below",
            "Below this score ARM sends a notification with Confirm / Pick another buttons and waits for you."),
    Setting("CONFIDENCE_SKIP_WAIT_ABOVE", 90, "int", "identify", "Don't wait above",
            "At or above this score ARM skips the manual-wait period and starts ripping right away. "
            "0 = always wait as usual."),
    Setting("LOW_CONFIDENCE_WAIT_TIME", 900, "int", "identify", "Wait for low confidence (seconds)",
            "How long to wait for you when the score is low. After that ARM rips with its best guess. "
            "0 = use MANUAL_WAIT_TIME."),
    Setting("RUNTIME_MATCHING", True, "bool", "identify", "Use the runtime to identify",
            "When the label is vague, search TMDb and pick the result whose runtime matches the main title."),

    # Before ripping
    Setting("ALREADY_RIPPED_ACTION", "notify", "choice", "before", "Disc already ripped",
            "What to do when the same disc was ripped before and its files still exist: "
            "rip = rip again, notify = rip and tell you, skip = eject without ripping.",
            ("rip", "notify", "skip")),
    Setting("MIN_FREE_GB_DVD", 20, "int", "before", "Free space needed for a DVD (GB)",
            "Free space ARM needs in the raw, transcode and completed folders before ripping a DVD."),
    Setting("MIN_FREE_GB_BLURAY", 80, "int", "before", "Free space needed for a Blu-ray (GB)",
            "Same for Blu-rays."),
    Setting("DISK_SPACE_ACTION", "wait", "choice", "before", "When space is short",
            "wait = notify and check every 5 minutes for up to 2 hours, fail = stop the job, ignore = rip anyway.",
            ("wait", "fail", "ignore")),

    # Titles
    Setting("TITLE_ANALYSIS", True, "bool", "titles", "Analyse the disc's titles",
            "Work out which titles are the main feature, other cuts, language versions, duplicates, extras "
            "or TV episodes, rip only what's needed and name the files accordingly."),
    Setting("PREFERRED_LANGUAGE", "eng", "langs", "titles", "Preferred language",
            "ISO 639-2 code (eng, fra, deu, spa, ...). Picks the main version when a disc has the movie "
            "in several languages."),
    Setting("KEEP_ALTERNATE_CUTS", True, "bool", "titles", "Keep other cuts",
            "Also rip theatrical / extended / director's cuts. They're saved next to the movie as versions "
            "Jellyfin can switch between."),
    Setting("KEEP_OTHER_LANGUAGE_VERSIONS", False, "bool", "titles", "Keep other language versions",
            "Also rip the movie's versions in other languages. When ARM can't tell which version is yours, "
            "it keeps them all and asks."),
    Setting("SKIP_DUPLICATE_TITLES", True, "bool", "titles", "Skip duplicate titles",
            "Don't rip titles that are exact copies of another title (common on protected discs) or "
            "\"play all\" titles on TV discs."),
    Setting("ORGANIZE_EXTRAS", True, "bool", "titles", "Name extras",
            "Put extras in the extras folder with readable names instead of B1_t03.mkv."),
    Setting("TV_EPISODE_MATCHING", True, "bool", "titles", "Match TV episodes",
            "Match titles to episodes using TMDb episode runtimes and name them Show S01E03.mkv."),

    # Checks
    Setting("VERIFY_OUTPUT", True, "bool", "checks", "Check finished files",
            "Compare each finished file's length with the disc title before deleting the raw rip. "
            "If one is short, the raw files are kept and you're told."),
    Setting("VERIFY_TOLERANCE_SECONDS", 90, "int", "checks", "Allowed difference (seconds)",
            "How much shorter a file may be than its title before it counts as cut off."),
    Setting("READ_ERROR_RETRIES", 1, "int", "checks", "Retries after a MakeMKV failure",
            "Retry the rip this many times. If it still fails you get \"clean the disc or try the other drive\"."),
    Setting("STUCK_JOB_MINUTES", 30, "int", "checks", "Stuck after (minutes)",
            "A rip or transcode with no progress for this long counts as stuck. 0 = off."),
    Setting("STUCK_JOB_ACTION", "abandon", "choice", "checks", "When a job is stuck",
            "notify = tell you, abandon = stop it cleanly, eject and tell you.", ("notify", "abandon")),
    Setting("TRIM_TRACKS", False, "bool", "checks", "Keep only my languages",
            "Only keep audio and subtitle tracks in the languages below (plus commentary and forced subtitles) "
            "when transcoding with HandBrake."),
    Setting("KEEP_AUDIO_LANGUAGES", "eng", "langs", "checks", "Audio languages",
            "Comma-separated ISO 639-2 codes, e.g. eng,fra."),
    Setting("KEEP_SUBTITLE_LANGUAGES", "eng", "langs", "checks", "Subtitle languages",
            "Comma-separated ISO 639-2 codes."),

    # Jellyfin
    Setting("JELLYFIN_URL", "", "str", "jellyfin", "Jellyfin address",
            "e.g. http://192.168.1.50:8096. Leave empty to turn this off."),
    Setting("JELLYFIN_API_KEY", "", "secret", "jellyfin", "Jellyfin API key",
            "Create one in Jellyfin: Dashboard > API Keys."),
    Setting("JELLYFIN_MEDIA_ROOT", "/media", "str", "jellyfin", "Media folder in Jellyfin",
            "Where Jellyfin sees your NAS media share."),
    Setting("JELLYFIN_FOLDER_MAP", "movies=Movies;tv=TV Shows;unidentified=unidentified;music=Music",
            "str", "jellyfin", "Folder names",
            "ARM folder = folder under the media share, separated by semicolons."),
    Setting("JELLYFIN_VERIFY_MATCH", True, "bool", "jellyfin", "Check Jellyfin's match",
            "After the scan, check Jellyfin matched the same movie or show ARM ripped."),

    # Housekeeping
    Setting("DB_BACKUP_ENABLED", True, "bool", "housekeeping", "Back up the database daily", ""),
    Setting("DB_BACKUP_TIME", "03:00", "time", "housekeeping", "Backup time", "24-hour time, HH:MM."),
    Setting("DB_BACKUP_KEEP", 14, "int", "housekeeping", "Backups to keep", ""),
    Setting("DB_BACKUP_DIR", "", "str", "housekeeping", "Backup folder",
            "Empty = a backups folder next to the database."),
    Setting("CLEANUP_ENABLED", True, "bool", "housekeeping", "Clean up daily",
            "Remove leftover raw and transcode folders that no job is using."),
    Setting("CLEANUP_LEFTOVER_DAYS", 3, "int", "housekeeping", "Leftovers older than (days)", ""),
    Setting("CLEANUP_FAILED_DUPLICATE_JOBS", True, "bool", "housekeeping", "Remove superseded failed jobs",
            "Delete failed jobs from the database once the same disc has been ripped successfully."),
    Setting("DAILY_SUMMARY_ENABLED", True, "bool", "housekeeping", "Daily summary",
            "One notification a day: what got ripped, what's unidentified and what failed."),
    Setting("DAILY_SUMMARY_TIME", "08:00", "time", "housekeeping", "Summary time", "24-hour time, HH:MM."),
)

BY_KEY = {setting.key: setting for setting in SCHEMA}
SECRET_KEYS = tuple(s.key for s in SCHEMA if s.kind == "secret")


def coerce(setting, value):
    """Turn a value from arm.yaml or a form into the setting's type"""
    if value is None or value == "None":
        value = setting.default
    if setting.kind == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if setting.kind == "int":
        try:
            return int(str(value).strip())
        except ValueError:
            return setting.default
    if setting.kind == "choice":
        value = str(value).strip().lower()
        return value if value in setting.choices else setting.default
    if setting.kind == "time":
        value = str(value).strip()
        try:
            hours, minutes = (int(part) for part in value.split(":"))
            if 0 <= hours < 24 and 0 <= minutes < 60:
                return f"{hours:02d}:{minutes:02d}"
        except ValueError:
            pass
        return setting.default
    if setting.kind == "langs":
        return ",".join(part.strip().lower() for part in str(value).split(",") if part.strip())
    return str(value).strip()


def get(key):
    """Current value of an automation setting"""
    import arm.config.config as cfg
    setting = BY_KEY[key]
    return coerce(setting, cfg.arm_config.get(key, setting.default))


def languages(key):
    """A langs setting as a list"""
    return [part for part in get(key).split(",") if part]


def ntfy_enabled():
    return bool(get("NTFY_SERVER") and get("NTFY_TOPIC"))


def jellyfin_enabled():
    return bool(get("JELLYFIN_URL") and get("JELLYFIN_API_KEY"))
