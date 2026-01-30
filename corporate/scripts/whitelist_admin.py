#!/usr/bin/env python3
"""
Whitelist Administration CLI Tool.

This script provides command-line management of the project whitelist
for the Corporate DMZ API.

Usage:
    python whitelist_admin.py add <PROJECT_CODE> [--disabled]
    python whitelist_admin.py enable <PROJECT_CODE>
    python whitelist_admin.py disable <PROJECT_CODE>
    python whitelist_admin.py remove <PROJECT_CODE>
    python whitelist_admin.py list
    python whitelist_admin.py check <PROJECT_CODE>

Environment Variables:
    WHITELIST_FILE_PATH: Path to JSON whitelist file (default: ./data/whitelist.json)

Examples:
    # Add a new project (enabled by default)
    python whitelist_admin.py add AAA

    # Add a project but keep it disabled initially
    python whitelist_admin.py add BBB --disabled

    # Disable an existing project
    python whitelist_admin.py disable AAA

    # Enable a disabled project
    python whitelist_admin.py enable AAA

    # Remove a project entirely
    python whitelist_admin.py remove BBB

    # List all projects
    python whitelist_admin.py list

    # Check if a project is allowed
    python whitelist_admin.py check AAA

You can also edit the whitelist.json file directly:
    {
      "projects": {
        "AAA": {"enabled": true},
        "BBB": {"enabled": false}
      }
    }
"""
import argparse
import os
import re
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.whitelist import ProjectWhitelist, WhitelistError


def validate_project_code(code: str) -> bool:
    """Validate project code format (3 uppercase alphanumeric characters)."""
    return bool(re.match(r"^[A-Z0-9]{3}$", code))


def cmd_add(args, whitelist: ProjectWhitelist) -> int:
    """Add a project to the whitelist."""
    project_code = args.project_code.upper()

    if not validate_project_code(project_code):
        print(f"Error: Invalid project code '{project_code}'")
        print("Project code must be exactly 3 uppercase alphanumeric characters")
        return 1

    try:
        enabled = not args.disabled
        whitelist.add_project(project_code, enabled=enabled)
        status = "enabled" if enabled else "disabled"
        print(f"Added project '{project_code}' ({status})")
        return 0
    except WhitelistError as e:
        print(f"Error: {e}")
        return 1


def cmd_enable(args, whitelist: ProjectWhitelist) -> int:
    """Enable a project in the whitelist."""
    project_code = args.project_code.upper()

    if not validate_project_code(project_code):
        print(f"Error: Invalid project code '{project_code}'")
        return 1

    if whitelist.enable_project(project_code):
        print(f"Enabled project '{project_code}'")
        return 0
    else:
        print(f"Error: Project '{project_code}' not found")
        return 1


def cmd_disable(args, whitelist: ProjectWhitelist) -> int:
    """Disable a project in the whitelist."""
    project_code = args.project_code.upper()

    if not validate_project_code(project_code):
        print(f"Error: Invalid project code '{project_code}'")
        return 1

    if whitelist.disable_project(project_code):
        print(f"Disabled project '{project_code}'")
        return 0
    else:
        print(f"Error: Project '{project_code}' not found")
        return 1


def cmd_remove(args, whitelist: ProjectWhitelist) -> int:
    """Remove a project from the whitelist."""
    project_code = args.project_code.upper()

    if not validate_project_code(project_code):
        print(f"Error: Invalid project code '{project_code}'")
        return 1

    if whitelist.remove_project(project_code):
        print(f"Removed project '{project_code}'")
        return 0
    else:
        print(f"Error: Project '{project_code}' not found")
        return 1


def cmd_list(args, whitelist: ProjectWhitelist) -> int:
    """List all projects in the whitelist."""
    projects = whitelist.list_projects()

    if not projects:
        print("No projects in whitelist")
        print(f"\nWhitelist file: {whitelist.file_path}")
        return 0

    print(f"{'Project':<12} {'Status':<10}")
    print("-" * 22)
    for code, enabled in projects:
        status = "enabled" if enabled else "disabled"
        print(f"{code:<12} {status:<10}")

    print(f"\nTotal: {len(projects)} project(s)")
    print(f"Whitelist file: {whitelist.file_path}")
    return 0


def cmd_check(args, whitelist: ProjectWhitelist) -> int:
    """Check if a project is allowed."""
    project_code = args.project_code.upper()

    if not validate_project_code(project_code):
        print(f"Error: Invalid project code '{project_code}'")
        return 1

    if whitelist.is_project_allowed(project_code):
        print(f"Project '{project_code}' is ALLOWED")
        return 0
    else:
        print(f"Project '{project_code}' is NOT ALLOWED")
        return 1


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Manage the project whitelist for Corporate DMZ API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s add AAA           Add project AAA (enabled)
  %(prog)s add BBB --disabled Add project BBB (disabled)
  %(prog)s enable AAA        Enable project AAA
  %(prog)s disable AAA       Disable project AAA
  %(prog)s remove AAA        Remove project AAA
  %(prog)s list              List all projects
  %(prog)s check AAA         Check if AAA is allowed

You can also edit the whitelist.json file directly.
        """
    )

    parser.add_argument(
        "--file",
        help="Path to whitelist JSON file (default: from WHITELIST_FILE_PATH env or ./data/whitelist.json)",
        default=None
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Add command
    add_parser = subparsers.add_parser("add", help="Add a project to the whitelist")
    add_parser.add_argument("project_code", help="3-character project code (A-Z0-9)")
    add_parser.add_argument(
        "--disabled",
        action="store_true",
        help="Add the project in disabled state"
    )

    # Enable command
    enable_parser = subparsers.add_parser("enable", help="Enable a project")
    enable_parser.add_argument("project_code", help="3-character project code")

    # Disable command
    disable_parser = subparsers.add_parser("disable", help="Disable a project")
    disable_parser.add_argument("project_code", help="3-character project code")

    # Remove command
    remove_parser = subparsers.add_parser("remove", help="Remove a project from whitelist")
    remove_parser.add_argument("project_code", help="3-character project code")

    # List command
    subparsers.add_parser("list", help="List all projects")

    # Check command
    check_parser = subparsers.add_parser("check", help="Check if a project is allowed")
    check_parser.add_argument("project_code", help="3-character project code")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Initialize whitelist
    whitelist = ProjectWhitelist(file_path=args.file)

    # Dispatch to command handler
    commands = {
        "add": cmd_add,
        "enable": cmd_enable,
        "disable": cmd_disable,
        "remove": cmd_remove,
        "list": cmd_list,
        "check": cmd_check,
    }

    try:
        return commands[args.command](args, whitelist)
    finally:
        whitelist.close()


if __name__ == "__main__":
    sys.exit(main())
