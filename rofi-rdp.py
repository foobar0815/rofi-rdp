#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import shutil
import sys
import subprocess
import yaml

# ==============================================================================
# USER SETTINGS (DEFAULTS)
# ==============================================================================
# Users can adjust their local default session settings here
KEYBOARD_LAYOUT = "0x00010407"  # German (IBM)
WINDOW_WIDTH = "1440"
WINDOW_HEIGHT = "900"
# None searches for an SDL client; otherwise set an executable name or path.
# Example: RDP_CLIENT = "xfreerdp"
RDP_CLIENT = None
# ==============================================================================


def determine_rdp_client():
    """Find the configured client or prefer explicitly named SDL3 clients."""
    candidates = (
        (RDP_CLIENT,) if RDP_CLIENT is not None else
        ("sdl3-freerdp3", "sdl3-freerdp", "sdl-freerdp3", "sdl-freerdp")
    )
    # Generic SDL names may refer to SDL2 or SDL3, depending on the package.
    for name in candidates:
        executable = shutil.which(name)
        if executable:
            return executable
    raise RuntimeError(
        "No executable FreeRDP client found. Tried: " + ", ".join(candidates)
        + ". Install an SDL3-based FreeRDP client or set RDP_CLIENT."
    )


def determine_client_environment():
    """Force native Wayland for SDL in Wayland sessions, without X11 fallback."""
    env = os.environ.copy()
    session_type = env.get("XDG_SESSION_TYPE", "").lower()
    if session_type == "wayland" or (
        not session_type and env.get("WAYLAND_DISPLAY")
    ):
        # SDL3 and SDL2 use different names. Non-SDL clients ignore these.
        env["SDL_VIDEO_DRIVER"] = "wayland"
        env["SDL_VIDEODRIVER"] = "wayland"
    return env


def determine_yaml_path():
    """
    Determines the path to the hostgroups.yaml file following XDG standards
    with a local fallback in the script's directory.
    """
    # 1. Check XDG_CONFIG_HOME (Defaults to ~/.config if not set)
    xdg_config = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    xdg_path = os.path.join(xdg_config, "rofi-rdp", "hostgroups.yaml")

    # 2. Check local path in the script's directory
    local_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "hostgroups.yaml"
    )

    # Return XDG path if it exists, otherwise fallback to local path
    return xdg_path if os.path.exists(xdg_path) else local_path


def main():
    yaml_file = determine_yaml_path()

    if not os.path.exists(yaml_file):
        print("Error: 'hostgroups.yaml' not found.")
        print("Please place the file in one of the following locations:")
        print(
            f" - {os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hostgroups.yaml')} (Local)"
        )
        print(" - or ~/.config/rofi-rdp/hostgroups.yaml (XDG)")
        sys.exit(1)

    with open(yaml_file, "r") as file:
        try:
            data = yaml.safe_load(file) or {}
        except yaml.YAMLError as exc:
            print(f"Error parsing YAML file: {exc}")
            sys.exit(1)

    # Rofi passes the user selection as the first argument
    selection = sys.argv[1] if len(sys.argv) > 1 else ""

    if not selection:
        # STEP 1: No selection yet -> List all hostgroups/customers
        display_groups(data)
    elif " => " in selection:
        # STEP 3: A host was selected -> Establish RDP connection
        group, host_displayname = selection.split(" => ", 1)
        connect_to_host(data, group, host_displayname)
    elif selection in data:
        # STEP 2: A group was selected -> List all hosts within that group
        display_hosts(selection, data[selection])
    else:
        print("Invalid selection")
        sys.exit(1)


def display_groups(data):
    """Lists all primary keys (groups/customers) to Rofi."""
    print("\x00prompt\x1fSelect Group/Customer\n")
    for group in data.keys():
        print(group)


def display_hosts(group, group_data):
    """Lists all hosts belonging to the selected group."""
    print(f"\x00prompt\x1fServers for {group}\n")

    hosts = group_data.get("hosts", [])
    if not hosts:
        print("No hosts found for this group.")
        sys.exit(0)

    # Pass the format "Group => Host Display Name" to Rofi
    for host in hosts:
        if "displayname" in host:
            print(f"{group} => {host['displayname']}")


def connect_to_host(data, group, host_displayname):
    """Resolves credentials and launches the selected RDP client."""
    group_data = data.get(group)
    if not group_data:
        sys.exit(1)

    # Find the matching host inside the hosts array
    hosts = group_data.get("hosts", [])
    host = next((h for h in hosts if h.get("displayname") == host_displayname), None)
    if not host:
        sys.exit(1)

    try:
        client = determine_rdp_client()
    except RuntimeError as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    # Fallback logic for credentials (Host-specific -> Group Defaults)
    defaults = group_data.get("defaults", {})

    address = host.get("address")
    username = host.get("username") or defaults.get("username")
    domain = host.get("domain") or defaults.get("domain")

    # Fetch and sanitize password from YAML
    yaml_password = str(host.get("password") or defaults.get("password") or "").strip()

    if not yaml_password:
        # If password is missing in YAML, look it up via secret-tool
        lookup_user = f"{username}@{domain}" if domain else username
        try:
            secret_output = subprocess.check_output(
                ["secret-tool", "lookup", "application", "rdphelper", "username", lookup_user],
                text=True
            ).strip()
        except subprocess.CalledProcessError:
            secret_output = ""

        if not secret_output:
            print(f"RDP connection aborted: No password found for {username} in YAML or Keyring.")
            sys.exit(1)

        password = secret_output
    else:
        password = yaml_password

    if not address:
        print(f"Error: No address defined for {host_displayname}.")
        sys.exit(1)

    # Build the FreeRDP command arguments array
    args = [
        client,
        f"/u:{username}",
        f"/v:{address}",
        f"/kbd:{KEYBOARD_LAYOUT}",
        f"/w:{WINDOW_WIDTH}",
        f"/h:{WINDOW_HEIGHT}",
        "/dynamic-resolution",
        "/cert:ignore",
        "+auto-reconnect",
        "/auto-reconnect-max-retries:5"
    ]

    # ONLY append credentials if they actually have content to avoid FreeRDP syntax errors
    if password and password.strip():
        args.append(f"/p:{password}")
    if domain and domain.strip():
        args.append(f"/d:{domain}")

    # Launch the client asynchronously to allow Rofi to close instantly
    subprocess.Popen(
        args, env=determine_client_environment(),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    
    sys.exit(0)


if __name__ == "__main__":
    main()
