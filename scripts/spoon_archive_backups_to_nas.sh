#!/usr/bin/env bash
set -euo pipefail

src="${SRC:-/home/spoon/backups/}"
nas_root="${NAS_ROOT:-/mnt/unas-llms/SpoonArchive}"
batch="${BATCH:-$(date +%F)-backups-gex-daily}"
dest="$nas_root/backups/$batch/backups"
meta="$nas_root/manifests/$batch"
log="$meta/archive.log"

mkdir -p "$dest" "$meta"

{
  echo "START $(date -Is)"
  echo "source=$src"
  echo "dest=$dest"

  echo "pre_df_source"
  df -hT "$src"
  echo "pre_df_dest"
  df -hT "$nas_root"

  echo "source_size"
  du -sh "$src"

  echo "source_manifest_start $(date -Is)"
  (cd "$src" && find . -type f -printf "%P\t%s\t%T@\n" | sort) > "$meta/source.files"
  (cd "$src" && find . -type f | wc -l) > "$meta/source.count"

  echo "rsync_start $(date -Is)"
  rsync -aH --partial --append-verify --info=progress2 --human-readable "$src" "$dest/"

  echo "dest_manifest_start $(date -Is)"
  (cd "$dest" && find . -type f -printf "%P\t%s\t%T@\n" | sort) > "$meta/dest.files"
  (cd "$dest" && find . -type f | wc -l) > "$meta/dest.count"

  echo "manifest_compare"
  if cmp -s "$meta/source.files" "$meta/dest.files"; then
    echo "MANIFEST_SIZE_MTIME_OK"
  else
    echo "MANIFEST_SIZE_MTIME_MISMATCH"
    diff -u "$meta/source.files" "$meta/dest.files" | head -200
    exit 2
  fi

  echo "checksum_source_start $(date -Is)"
  (cd "$src" && find . -type f -print0 | sort -z | xargs -0 sha256sum) > "$meta/source.sha256"

  echo "checksum_dest_start $(date -Is)"
  (cd "$dest" && find . -type f -print0 | sort -z | xargs -0 sha256sum) > "$meta/dest.sha256"

  echo "checksum_compare"
  if cmp -s "$meta/source.sha256" "$meta/dest.sha256"; then
    echo "CHECKSUM_OK"
  else
    echo "CHECKSUM_MISMATCH"
    diff -u "$meta/source.sha256" "$meta/dest.sha256" | head -200
    exit 3
  fi

  echo "post_df_source"
  df -hT "$src"
  echo "post_df_dest"
  df -hT "$nas_root"
  echo "DELETE_NOT_PERFORMED source still at $src"
  echo "DONE $(date -Is)"
} >> "$log" 2>&1
