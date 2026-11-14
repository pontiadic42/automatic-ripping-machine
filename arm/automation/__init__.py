"""
Automation for ARM: identification confidence and phone confirmation, already-ripped
detection, disk space checks, title analysis (cuts, language versions, duplicates,
extras, TV episodes), output checks, Jellyfin refresh, and housekeeping (database
backups, cleanup, stuck-job watchdog, daily summary).

Every feature reads its settings through ``arm.automation.settings`` and can be
switched off in arm.yaml or on the Settings > Automation page.
"""
