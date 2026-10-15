#!/bin/bash

# Recover from stale apt/dpkg locks left behind by prior runs.
cleanup_apt_state() {
    local signal pid comm found proc_dir

    found=0
    for _ in 1 2 3 4 5; do
        found=0
        for proc_dir in /proc/[0-9]*; do
            [ -r "$proc_dir/comm" ] || continue
            read -r comm < "$proc_dir/comm" || continue
            case "$comm" in
                apt|apt-get|apt-helper|dpkg|unattended-upgr|unattended-upgrade)
                    found=1
                    break
                    ;;
            esac
        done
        [ "$found" -eq 0 ] && break
        sleep 2
    done

    if [ "$found" -ne 0 ]; then
        for signal in TERM KILL; do
            for proc_dir in /proc/[0-9]*; do
                [ -r "$proc_dir/comm" ] || continue
                read -r comm < "$proc_dir/comm" || continue
                case "$comm" in
                    apt|apt-get|apt-helper|dpkg|unattended-upgr|unattended-upgrade)
                        pid=${proc_dir#/proc/}
                        echo "Stopping stale package manager process $comm ($pid) with SIG$signal"
                        kill "-$signal" "$pid" 2>/dev/null || true
                        ;;
                esac
            done
            sleep 2
        done
    fi

    found=0
    for proc_dir in /proc/[0-9]*; do
        [ -r "$proc_dir/comm" ] || continue
        read -r comm < "$proc_dir/comm" || continue
        case "$comm" in
            apt|apt-get|apt-helper|dpkg|unattended-upgr|unattended-upgrade)
                found=1
                break
                ;;
        esac
    done

    if [ "$found" -eq 0 ]; then
        rm -f \
            /var/lib/dpkg/lock-frontend \
            /var/lib/dpkg/lock \
            /var/cache/apt/archives/lock \
            /var/lib/apt/lists/lock

        if command -v dpkg >/dev/null 2>&1; then
            dpkg --configure -a >/dev/null 2>&1 || true
        fi
    else
        echo "Warning: apt/dpkg processes are still running; apt commands may still block"
    fi
}

cleanup_apt_state

mkdir -p /logs/verifier

# Check if we're in a valid working directory
if [ "$PWD" = "/" ]; then
    echo "Error: No working directory set. Please set a WORKDIR in your Dockerfile before running this script."
    exit 1
fi

# The test run needs python3 with pytest, pytest-json-ctrf and pyyaml.
# Prefer dependencies already present in the container so no network access
# is required; only try to install what is actually missing.
pytest_ready() {
    command -v python3 >/dev/null 2>&1 || return 1
    python3 -c "
import importlib.metadata as md, sys
try:
    for dist in ('pytest', 'pytest-json-ctrf', 'pyyaml'):
        md.version(dist)
except md.PackageNotFoundError:
    sys.exit(1)
" >/dev/null 2>&1
}

install_uv() {
    command -v uv >/dev/null 2>&1 && return 0
    if ! command -v curl >/dev/null 2>&1; then
        apt-get update || echo "Warning: apt-get update failed; continuing anyway"
        apt-get install -y curl || echo "Warning: failed to install curl"
    fi
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/0.9.5/install.sh | sh || echo "Warning: uv installation failed (possibly no network)"
    fi
    if [ -f "$HOME/.local/bin/env" ]; then
        . "$HOME/.local/bin/env"
    fi
    command -v uv >/dev/null 2>&1
}

PYTEST_ARGS="--ctrf /logs/verifier/ctrf.json /tests/test_outputs.py -rA"

if pytest_ready; then
    echo "pytest and required plugins already available; skipping dependency installation"
    python3 -m pytest $PYTEST_ARGS
elif install_uv; then
    uvx \
      -p 3.13 \
      -w pytest==8.4.1 \
      -w pytest-json-ctrf==0.3.5 \
      -w pyyaml==6.0.2 \
      pytest $PYTEST_ARGS
else
    echo "uv unavailable; trying pip install as fallback"
    python3 -m pip install pytest==8.4.1 pytest-json-ctrf==0.3.5 pyyaml==6.0.2 || echo "Warning: pip install failed (possibly no network)"
    if pytest_ready; then
        python3 -m pytest $PYTEST_ARGS
    else
        echo "Error: pytest with required plugins is unavailable and could not be installed (network unreachable?)"
        false
    fi
fi

if [ $? -eq 0 ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
