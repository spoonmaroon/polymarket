#!/usr/bin/env bash
set -euo pipefail

src_parent="${SRC_PARENT:-/home/spoon}"
src_name="${SRC_NAME:-backups}"
nas_root="${NAS_ROOT:-/mnt/unas-llms/SpoonArchive}"
batch="${BATCH:-$(date +%F)-backups-gex-daily-tar}"
dest="$nas_root/backups/$batch"
meta="$nas_root/manifests/$batch"
log="$meta/archive.log"
archive="$dest/backups.tar"
partial="$archive.partial"

mkdir -p "$dest" "$meta"

{
  echo "START $(date -Is)"
  echo "source=$src_parent/$src_name"
  echo "archive=$archive"

  echo "pre_df_source"
  df -hT "$src_parent/$src_name"
  echo "pre_df_dest"
  df -hT "$nas_root"

  echo "source_size"
  du -sh "$src_parent/$src_name"

  echo "source_summary_start $(date -Is)"
  find "$src_parent/$src_name" -xdev -type f -printf ".%P\t%s\t%T@\n" | sort > "$meta/source.summary"
  find "$src_parent/$src_name" -xdev -type f | wc -l > "$meta/source.count"

  echo "tar_start $(date -Is)"
  rm -f "$partial"
  tar -C "$src_parent" -cf "$partial" "$src_name"
  mv "$partial" "$archive"
  echo "tar_done $(date -Is)"
  ls -lh "$archive"

  echo "sha256_start $(date -Is)"
  sha256sum "$archive" > "$meta/backups.tar.sha256"

  echo "tar_compare_start $(date -Is)"
  tar -C "$src_parent" -df "$archive" "$src_name"
  echo "TAR_COMPARE_OK"

  echo "tar_list_summary_start $(date -Is)"
  tar -tf "$archive" | sed "s#^backups##; s#^/##" | sort > "$meta/archive.paths"

  echo "post_df_source"
  df -hT "$src_parent/$src_name"
  echo "post_df_dest"
  df -hT "$nas_root"
  echo "DELETE_NOT_PERFORMED source still at $src_parent/$src_name"
  echo "DONE $(date -Is)"
} >> "$log" 2>&1
