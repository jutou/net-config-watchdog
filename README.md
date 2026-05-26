# net-config-watchdog 🐕

**Network config backup + change detection tool for TAC engineers.**

A lightweight CLI tool that SSHes into your network devices, grabs running configs
and key show commands, snapshots them with timestamps, and diffs changes between
backups. Built for the daily reality of a TAC engineer — not for the SE PowerPoint.

---

## Why this exists

Every day in a TAC role you need:

- A quick "what changed on this device since yesterday"
- Reliable config snapshots before making changes
- A way to prove "that config was fine before the maintenance window"

Existing tools are either enterprise-grade overkill (CiscoWorks, DNAC) or
vendor-locked. This is neither. It's a single Python file that does one thing
well: **back up, diff, report.**

## Quick start

```bash
# 1. Install dependencies
pip install netmiko pyyaml rich

# 2. Edit inventory.yaml with your device details
#    (see inventory.yaml.example)

# 3. Run a backup
python net_watchdog.py backup

# 4. See what changed after a maintenance window
python net_watchdog.py diff

# 5. Generate a summary report
python net_watchdog.py report
```

## Commands

| Command | What it does |
|---------|-------------|
| `backup` | SSH into devices, collect configs + show commands, save timestamped snapshots |
| `diff` | Compare latest two snapshots, highlight changes (green/red terminal output) |
| `report` | Generate a summary overview: backup counts, change status per device |

Options:
- `--device RTR-01` — target a single device by its inventory name
- `--ask-pass` — prompt for passwords (don't store them in files)
- `--check-deps` — verify all dependencies are installed

## Example workflow

```bash
# Monday morning — backup everything
python net_watchdog.py backup

# Tuesday — engineer makes a routing change
# Wednesday — something broke, check what changed
python net_watchdog.py diff --device core-rtr-01

# Output:
#   core-rtr-01: ⚠  show running-config — 12 lines changed
#   --- core-rtr-01 (previous): show running-config
#   +++ core-rtr-01 (latest): show running-config
#   @@ -450,6 +450,10 @@
#   +router ospf 100
#   + network 10.0.0.0 0.255.255.255 area 0
#
# Gotcha — someone added OSPF without telling anyone.
```

## Project structure

```
net-config-watchdog/
├── net_watchdog.py      # Main tool (single file, ~400 lines)
├── inventory.yaml        # Your device inventory (edit this)
├── requirements.txt      # Python dependencies
├── backups/              # Timestamped config snapshots (auto-created)
│   ├── core-rtr-01/
│   │   ├── 20260526_091500__show_running-config.txt
│   │   └── 20260526_091500__show_ip_route.txt
│   └── edge-rtr-01/
├── reports/              # Generated reports (auto-created)
│   └── report_2026-05-26.txt
└── README.md
```

## Security

- **Passwords never stored in inventory.yaml.** Use one of:
  1. `NETDOG_SECRET` environment variable (preferred)
  2. `~/.netdog/credentials` file (chmod 600)
  3. `--ask-pass` flag (interactive prompt)
- No cloud, no telemetry, no vendor calls home.

## Roadmap / Nice-to-haves

- [ ] HTML report with syntax-highlighted diffs
- [ ] Email/Slack notification on config change
- [ ] Git-based storage (auto-commit snapshots)
- [ ] pyATS integration for structured parsing

*Want to contribute? PRs welcome. Keep it simple, keep it useful.*

---

*Built for network engineers who need answers fast.*