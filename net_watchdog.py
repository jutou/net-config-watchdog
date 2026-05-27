#!/usr/bin/env python3
"""
net-config-watchdog — Network Config Backup & Change Monitor

A tool for Network TAC engineers to:
  1. Backup running configs and key show commands from network devices
  2. Track configuration changes over time
  3. Generate human-readable diff reports

Designed for real-world TAC workflows: simple, focused, no vendor lock-in.

Usage:
  python net_watchdog.py backup              Backup all devices
  python net_watchdog.py backup --device X   Backup a single device
  python net_watchdog.py diff                Show changes since last backup
  python net_watchdog.py diff --device X     Changes for one device
  python net_watchdog.py report              Generate summary report
  python net_watchdog.py --ask-pass backup   Prompt for password (one-time)
  python net_watchdog.py --ask-pass --save-pass backup   Save password for future use
  python net_watchdog.py --demo              Simulate a backup cycle (no real devices needed)
  python net_watchdog.py --check-deps        Verify dependencies
"""

import argparse
import datetime
import difflib
import hashlib
import os
import sys
import textwrap
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent
BACKUP_DIR = PROJECT_DIR / "backups"
REPORT_DIR = PROJECT_DIR / "reports"
INVENTORY_FILE = PROJECT_DIR / "inventory.yaml"

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def timestamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

def date_stamp() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d")

def device_backup_path(device_name: str) -> Path:
    """Return the per-device backup directory, creating if needed."""
    p = BACKUP_DIR / device_name
    p.mkdir(parents=True, exist_ok=True)
    return p

# ---------------------------------------------------------------------------
# Step 1 — Backup
# ---------------------------------------------------------------------------
def run_backup(args):
    """Main backup routine. Connects to devices, collects output, saves files."""
    inventory = _load_inventory()
    devices = _resolve_devices(inventory, args.device)

    if not devices:
        print("⚠  No devices found. Check inventory.yaml.")
        return

    success = 0
    failed = 0

    for dev in devices:
        print(f"  -> {dev['name']} ({dev['host']}) ... ", end="", flush=True)
        try:
            output = _collect_device_output(dev, ask_pass=args.ask_pass, save_pass=args.save_pass)
            _save_device_output(dev["name"], output)
            print("✅")
            success += 1
        except Exception as e:
            print(f"❌  {e}")
            failed += 1

    print(f"\nDone: {success} succeeded, {failed} failed.")

    # Count total backups to know if we can diff
    total_snapshots = 0
    for dev in devices:
        dev_dir = device_backup_path(dev["name"])
        total_snapshots += len(list(dev_dir.glob("*__manifest.txt")))
    backup_count = total_snapshots // len(devices) if devices else 0

    if success and backup_count >= 2:
        print("\n--- Quick change summary ---")
        _run_diff(args, quiet=True)
    elif success and backup_count == 1 and not args.device:
        print("\n  (First backup complete. Run again to detect changes.)")


def _load_inventory():
    """Parse inventory.yaml via PyYAML (fallback: manual parse if missing)."""
    try:
        import yaml
    except ImportError:
        print("❌  PyYAML not installed. Run: pip install pyyaml")
        sys.exit(1)

    if not INVENTORY_FILE.exists():
        print(f"❌  Inventory not found: {INVENTORY_FILE}")
        print("   Copy inventory.yaml.example to inventory.yaml and edit.")
        sys.exit(1)

    with open(INVENTORY_FILE) as f:
        data = yaml.safe_load(f)

    devices = []
    for group_name, group in data.get("device_groups", {}).items():
        platform = group.get("platform", "cisco_xe")
        default_commands = group.get("commands", ["show running-config"])
        for dev in group.get("devices", []):
            dev["platform"] = platform
            if "commands" not in dev:
                dev["commands"] = default_commands
            dev["_group"] = group_name
            devices.append(dev)
    return devices


def _resolve_devices(inventory, device_name=None):
    """Filter inventory to one device if --device was passed."""
    if device_name:
        return [d for d in inventory if d["name"] == device_name]
    return inventory


def _collect_device_output(dev, ask_pass=False, save_pass=False):
    """Connect via Netmiko, collect configured show commands."""
    try:
        from netmiko import ConnectHandler
    except ImportError:
        print("❌  Netmiko not installed. Run: pip install netmiko")
        sys.exit(1)

    password = _get_password(dev, ask_pass, save_pass=save_pass)

    connection = ConnectHandler(
        device_type=dev.get("platform", "cisco_xe"),
        host=dev["host"],
        port=dev.get("port", 22),
        username=dev["username"],
        password=password,
        secret=password,  # enable password fallback
        global_delay_factor=dev.get("delay_factor", 1),
        timeout=dev.get("timeout", 30),
    )

    connection.enable()

    collected = {}
    for cmd in dev["commands"]:
        collected[cmd] = connection.send_command(cmd, delay_factor=dev.get("delay_factor", 1))

    connection.disconnect()
    return collected


def _get_password(dev, ask_pass, save_pass=False):
    """Password resolution: env var -> credential file -> --ask-pass."""
    env_pw = os.environ.get("NETDOG_SECRET")
    if env_pw:
        return env_pw

    cred_file = Path.home() / ".netdog" / "credentials"
    if cred_file.exists():
        with open(cred_file) as f:
            return f.read().strip()

    if ask_pass:
        try:
            import getpass
            pw = getpass.getpass(f"  Password for {dev['name']} ({dev['host']}): ")
            if save_pass and pw:
                cred_file.parent.mkdir(parents=True, exist_ok=True)
                with open(cred_file, "w") as f:
                    f.write(pw.strip())
                os.chmod(cred_file, 0o600)
                print(f"  (Password saved to {cred_file})")
            return pw
        except Exception:
            pass

    # Fallback: use device entry password if specified (not recommended for prod)
    return dev.get("password", "")


def _save_device_output(device_name, output):
    """Write collected output to timestamped files."""
    ts = timestamp()
    dev_dir = device_backup_path(device_name)
    manifest = []

    for cmd, text in output.items():
        safe_name = cmd.replace(" ", "_").replace("/", "_")
        filename = f"{ts}__{safe_name}.txt"
        filepath = dev_dir / filename
        header = f"# Command: {cmd}\n# Device: {device_name}\n# Time: {ts}\n# {'=' * 60}\n\n"
        with open(filepath, "w") as f:
            f.write(header + text)
        manifest.append((cmd, str(filepath)))

    # Save a manifest of what was collected
    manifest_path = dev_dir / f"{ts}__manifest.txt"
    with open(manifest_path, "w") as f:
        for cmd, path in manifest:
            f.write(f"{cmd} -> {path}\n")


# ---------------------------------------------------------------------------
# Step 2 — Diff
# ---------------------------------------------------------------------------
def run_diff(args):
    """Entry point for the diff command."""
    _run_diff(args, quiet=False)


def _run_diff(args, quiet=False):
    inventory = _load_inventory()
    devices = _resolve_devices(inventory, args.device)
    any_change = False

    for dev in devices:
        name = dev["name"]
        dev_dir = device_backup_path(name)
        if not dev_dir.exists() or not list(dev_dir.glob("*__manifest.txt")):
            if not quiet:
                print(f"  {name}: no backups yet. Run 'backup' first.")
            continue

        changes = _diff_device(dev)
        if changes:
            any_change = True
            for cmd, diff_lines in changes:
                if diff_lines:
                    _print_diff(name, cmd, diff_lines, quiet)
        elif not quiet:
            print(f"  {name}: ✅  no changes")

    if not any_change:
        if not quiet:
            print("\n  ✅  All devices: no configuration changes detected.")
    else:
        print("\n  ⚠  Changes detected! Run 'report' for detailed output.")


def _diff_device(dev):
    """Compare latest two backups for each show command on a device."""
    name = dev["name"]
    dev_dir = device_backup_path(name)

    # Find all backup timestamps from manifest files
    manifests = sorted(dev_dir.glob("*__manifest.txt"))
    if len(manifests) < 2:
        return None  # Not enough data to diff

    latest = manifests[-1]
    previous = manifests[-2]

    # Parse manifests into command -> file path mappings
    def parse_manifest(m_path):
        mapping = {}
        with open(m_path) as f:
            for line in f:
                if " -> " in line:
                    cmd, path = line.strip().split(" -> ", 1)
                    mapping[cmd] = path
        return mapping

    latest_map = parse_manifest(latest)
    previous_map = parse_manifest(previous)

    changes = []
    for cmd in dev.get("commands", []):
        latest_file = latest_map.get(cmd)
        previous_file = previous_map.get(cmd)
        if not latest_file or not previous_file:
            continue

        with open(latest_file) as f:
            latest_text = f.readlines()
        with open(previous_file) as f:
            previous_text = f.readlines()

        diff = list(difflib.unified_diff(
            previous_text,
            latest_text,
            fromfile=f"{name} (previous): {cmd}",
            tofile=f"{name} (latest): {cmd}",
            lineterm="",
        ))
        changes.append((cmd, diff))

    return changes


def _print_diff(name, cmd, diff_lines, quiet=False):
    if quiet:
        print(f"  {name}: ⚠  {cmd} — {len(diff_lines)} lines changed")
        return

    print(f"\n{'=' * 60}")
    print(f"  Device: {name}")
    print(f"  Command: {cmd}")
    print(f"{'=' * 60}")
    for line in diff_lines:
        if line.startswith("+"):
            print(f"\033[32m{line}\033[0m")  # green
        elif line.startswith("-"):
            print(f"\033[31m{line}\033[0m")  # red
        elif line.startswith("@@"):
            print(f"\033[36m{line}\033[0m")  # cyan
        else:
            print(line)


# ---------------------------------------------------------------------------
# Step 3 — Report
# ---------------------------------------------------------------------------
def run_report(args):
    """Generate a summary report of all backup activity and recent changes."""
    inventory = _load_inventory()
    devices = _resolve_devices(inventory, args.device)
    today = date_stamp()
    report_lines = [
        "=" * 66,
        "  NET-CONFIG-WATCHDOG — Backup & Change Report",
        f"  Generated: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 66,
        "",
    ]

    total_backups = 0
    changed_devices = 0

    for dev in devices:
        name = dev["name"]
        dev_dir = device_backup_path(name)
        manifests = sorted(dev_dir.glob("*__manifest.txt"))
        count = len(manifests)
        total_backups += count

        report_lines.append(f"  {name} ({dev['host']})")
        report_lines.append(f"    Group: {dev.get('_group', 'N/A')}")
        report_lines.append(f"    Platform: {dev.get('platform', 'N/A')}")
        report_lines.append(f"    Backups: {count} snapshot(s)")

        if count >= 2:
            changes = _diff_device(dev)
            if changes and any(d for _, d in changes if d):
                changed_devices += 1
                report_lines.append(f"    ⚠  Changes detected!")
                for cmd, diff in changes:
                    if diff:
                        report_lines.append(f"      -> {cmd}: {len(diff)} lines affected")
            else:
                report_lines.append(f"    ✅  No recent changes")
        else:
            report_lines.append(f"    ➖  Need at least 2 backups for diff")

        report_lines.append("")

    report_lines.append("=" * 66)
    report_lines.append(f"  Summary: {len(devices)} devices, {total_backups} total backups")
    report_lines.append(f"  Changed since last backup: {changed_devices} device(s)")
    report_lines.append("=" * 66)

    report_text = "\n".join(report_lines)

    # Save report
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_file = REPORT_DIR / f"report_{today}.txt"
    with open(report_file, "w") as f:
        f.write(report_text)
    print(report_text)
    print(f"\n  Report saved: {report_file}")


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------
def run_demo(args):
    """Generate simulated backups and a diff to demonstrate the tool — no real devices needed."""
    import random

    print("=" * 60)
    print("  NET-CONFIG-WATCHDOG — Demo Mode")
    print("  Creating simulated backups for 2 devices...")
    print("=" * 60)

    # --- Device 1: core-rtr-01 ---
    dev_dir = device_backup_path("core-rtr-01")
    ts1 = "20260526_090000"
    ts2 = "20260526_170000"

    config_before = """!
! Running config — core-rtr-01
! Backup time: {ts1}
!
hostname core-rtr-01
!
interface GigabitEthernet0/0
 ip address 192.168.1.1 255.255.255.0
 no shutdown
!
interface GigabitEthernet0/1
 ip address 10.0.0.1 255.255.255.0
 no shutdown
!
router ospf 100
 network 192.168.1.0 0.0.0.255 area 0
 network 10.0.0.0 0.0.0.255 area 0
!
ip route 0.0.0.0 0.0.0.0 192.168.1.254
""".format(ts1=ts1)

    config_after = """!
! Running config — core-rtr-01
! Backup time: {ts2}
!
hostname core-rtr-01
!
interface GigabitEthernet0/0
 ip address 192.168.1.1 255.255.255.0
 no shutdown
!
interface GigabitEthernet0/1
 ip address 10.0.0.1 255.255.255.0
 no shutdown
!
interface GigabitEthernet0/2
 ip address 172.16.0.1 255.255.255.0
 no shutdown
!
router ospf 100
 network 192.168.1.0 0.0.0.255 area 0
 network 10.0.0.0 0.0.0.255 area 0
 network 172.16.0.0 0.0.0.255 area 0
!
ip route 0.0.0.0 0.0.0.0 192.168.1.254
""".format(ts2=ts2)

    # Write "before" backup
    with open(dev_dir / f"{ts1}__show_running-config.txt", "w") as f:
        f.write(config_before)
    with open(dev_dir / f"{ts1}__show_ip_route.txt", "w") as f:
        f.write("""Codes: C - connected, S - static, O - OSPF\nC   192.168.1.0/24 is directly connected, GigabitEthernet0/0\nC   10.0.0.0/24 is directly connected, GigabitEthernet0/1\nS   0.0.0.0/0 [1/0] via 192.168.1.254\n""")
    with open(dev_dir / f"{ts1}__manifest.txt", "w") as f:
        f.write(f"show running-config -> {dev_dir}/{ts1}__show_running-config.txt\nshow ip route -> {dev_dir}/{ts1}__show_ip_route.txt\n")

    # Write "after" backup (with changes!)
    with open(dev_dir / f"{ts2}__show_running-config.txt", "w") as f:
        f.write(config_after)
    with open(dev_dir / f"{ts2}__show_ip_route.txt", "w") as f:
        f.write("""Codes: C - connected, S - static, O - OSPF\nC   192.168.1.0/24 is directly connected, GigabitEthernet0/0\nC   10.0.0.0/24 is directly connected, GigabitEthernet0/1\nC   172.16.0.0/24 is directly connected, GigabitEthernet0/2\nS   0.0.0.0/0 [1/0] via 192.168.1.254\n""")
    with open(dev_dir / f"{ts2}__manifest.txt", "w") as f:
        f.write(f"show running-config -> {dev_dir}/{ts2}__show_running-config.txt\nshow ip route -> {dev_dir}/{ts2}__show_ip_route.txt\n")

    print(f"  ✅  core-rtr-01: 2 snapshots created (1 change injected)")

    # --- Device 2: edge-rtr-01 (no changes — to show "no changes") ---
    dev_dir2 = device_backup_path("edge-rtr-01")
    with open(dev_dir2 / f"{ts1}__show_running-config.txt", "w") as f:
        f.write("""!\n! Running config — edge-rtr-01\n!\nhostname edge-rtr-01\n!\ninterface GigabitEthernet0/0\n ip address 10.0.1.1 255.255.255.0\n no shutdown\n!\nrouter bgp 65000\n neighbor 10.0.1.254 remote-as 65001\n!\n""")
    with open(dev_dir2 / f"{ts1}__manifest.txt", "w") as f:
        f.write(f"show running-config -> {dev_dir2}/{ts1}__show_running-config.txt\n")

    with open(dev_dir2 / f"{ts2}__show_running-config.txt", "w") as f:
        f.write("""!\n! Running config — edge-rtr-01\n!\nhostname edge-rtr-01\n!\ninterface GigabitEthernet0/0\n ip address 10.0.1.1 255.255.255.0\n no shutdown\n!\nrouter bgp 65000\n neighbor 10.0.1.254 remote-as 65001\n!\n""")
    with open(dev_dir2 / f"{ts2}__manifest.txt", "w") as f:
        f.write(f"show running-config -> {dev_dir2}/{ts2}__show_running-config.txt\n")

    print(f"  ✅  edge-rtr-01: 2 snapshots created (no changes)")
    print()

    # Now run diff
    print("=" * 60)
    print("  DEMO DIFF — changes detected on core-rtr-01:")
    print("=" * 60)
    args.device = None
    _run_diff(args, quiet=False)

    # Print key takeaways
    print()
    print("=" * 60)
    print("  DEMO COMPLETE")
    print("=" * 60)
    print("  What just happened:")
    print("    1. Simulated 2x backup runs for core-rtr-01 & edge-rtr-01")
    print("    2. Injected a config change on core-rtr-01:")
    print("       + Added GigabitEthernet0/2 with IP 172.16.0.1/24")
    print("       + Added OSPF network 172.16.0.0/24 area 0")
    print("    3. 'diff' detected the change and showed green/red lines")
    print("    4. edge-rtr-01 showed 'no changes'")
    print()
    print("  Next steps:")
    print("    $ python net_watchdog.py report    -> Full summary")
    print("    $ pip install netmiko pyyaml       -> Install deps for real use")
    print("    $ edit inventory.yaml              -> Add your real devices")
    print("    $ python net_watchdog.py backup    -> Backup for real")
    print()
    print("  Clean up demo data:")
    print("    $ rm -rf backups/")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        prog="net-watchdog",
        description="Network Config Backup & Change Monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python net_watchdog.py backup                Backup all devices
              python net_watchdog.py backup --device X     Backup one device
              python net_watchdog.py diff                  Show changes
              python net_watchdog.py report                Summary report
              python net_watchdog.py --ask-pass backup     Prompt for passwords
        """),
    )
    parser.add_argument("--ask-pass", action="store_true", help="Prompt for passwords")
    parser.add_argument("--save-pass", action="store_true", help="Save password from --ask-pass to ~/.netdog/credentials")
    parser.add_argument("--device", help="Target a single device by name")
    parser.add_argument("--check-deps", action="store_true", help="Check dependencies")
    parser.add_argument("--demo", action="store_true", help="Run demo cycle (no real devices)")

    sub = parser.add_subparsers(dest="command", help="Available commands")

    sub.add_parser("backup", help="Collect configs from all/specified devices")
    sub.add_parser("diff", help="Show config changes since last backup")
    sub.add_parser("report", help="Generate a summary report")

    args = parser.parse_args()

    if args.check_deps:
        _check_deps()
        return

    if args.demo:
        run_demo(args)
        return

    if not args.command:
        parser.print_help()
        return

    commands = {
        "backup": run_backup,
        "diff": run_diff,
        "report": run_report,
    }
    commands[args.command](args)


def _check_deps():
    """Verify that required packages are installed."""
    missing = []
    try:
        import yaml
    except ImportError:
        missing.append("PyYAML")
    try:
        import netmiko
    except ImportError:
        missing.append("netmiko")
    try:
        import rich
    except ImportError:
        pass  # optional

    if missing:
        print(f"❌  Missing: {', '.join(missing)}")
        print(f"   Install: pip install {' '.join(missing)}")
    else:
        print("✅  All dependencies satisfied.")


if __name__ == "__main__":
    main()