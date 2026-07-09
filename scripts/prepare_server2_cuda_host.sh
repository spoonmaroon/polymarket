#!/usr/bin/env bash
set -euo pipefail

MODE="${1:---dry-run}"
CONFIRM_DESTROY_WIN11_VM="${CONFIRM_DESTROY_WIN11_VM:-}"
BACKUP_DIR="/root/server2-vm-retirement"
VFIO_CONF="/etc/modprobe.d/vfio-passthrough.conf"

run() {
  if [ "$MODE" != "--execute" ]; then
    printf '[dry-run] %q' "$1"
    shift
    for arg in "$@"; do
      printf ' %q' "$arg"
    done
    printf '\n'
  else
    "$@"
  fi
}

if [ "$(id -u)" != "0" ]; then
  echo "run as root on server2" >&2
  exit 1
fi

host="$(hostname)"
if [ "$host" != "docker" ] && [ "$host" != "server2" ]; then
  echo "refusing to run on unexpected host: $host" >&2
  exit 1
fi

if [ "$MODE" != "--dry-run" ] && [ "$MODE" != "--execute" ]; then
  echo "usage: $0 [--dry-run|--execute]" >&2
  exit 2
fi

if virsh dominfo win11-gaming >/dev/null 2>&1; then
  state="$(virsh domstate win11-gaming | tr -d '\r')"
  if [ "$state" != "shut off" ]; then
    echo "win11-gaming must be shut off before retirement; current state: $state" >&2
    exit 1
  fi
  if [ "$MODE" = "--execute" ] && [ "$CONFIRM_DESTROY_WIN11_VM" != "destroy-win11-gaming" ]; then
    echo "set CONFIRM_DESTROY_WIN11_VM=destroy-win11-gaming to destroy the VM" >&2
    exit 1
  fi
  run mkdir -p "$BACKUP_DIR"
  if [ "$MODE" = "--execute" ]; then
    virsh dumpxml win11-gaming > "$BACKUP_DIR/win11-gaming.xml"
  else
    echo '[dry-run] virsh dumpxml win11-gaming > "$BACKUP_DIR/win11-gaming.xml"'
  fi
  run virsh undefine win11-gaming --nvram --remove-all-storage
fi

run mkdir -p "$BACKUP_DIR"
if [ -f "$VFIO_CONF" ]; then
  run mv /etc/modprobe.d/vfio-passthrough.conf "$BACKUP_DIR/vfio-passthrough.conf.backup"
fi

run update-initramfs -u
run apt-get update
run ubuntu-drivers install
run apt-get install -y nvidia-container-toolkit
run nvidia-ctk runtime configure --runtime=docker
run systemctl restart docker

echo "NEEDS_REBOOT=1"
