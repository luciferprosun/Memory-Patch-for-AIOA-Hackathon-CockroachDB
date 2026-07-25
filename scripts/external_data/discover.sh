#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' 'External-volume discovery (read-only; no device is selected automatically)'
printf '%s\n' 'lsblk properties:'
lsblk \
  -o NAME,PATH,TYPE,TRAN,MODEL,SERIAL,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINTS,RO,RM

printf '\n%s\n' 'Mounted real filesystems:'
findmnt --real

printf '\n%s\n' 'Filesystem capacity:'
df -hT

printf '\n%s\n' 'Exact byte capacity:'
df -B1 --output=source,fstype,size,used,avail,pcent,target

printf '\n%s\n' 'UUID links:'
find /dev/disk/by-uuid -maxdepth 1 -type l -printf '%f -> %l\n' 2>/dev/null |
  sort || true

printf '\n%s\n' 'Label links:'
find /dev/disk/by-label -maxdepth 1 -type l -printf '%f -> %l\n' 2>/dev/null |
  sort || true

printf '\n%s\n' \
  'Review transport, model, capacity, label, UUID, filesystem, mountpoint, and mount options together.'
