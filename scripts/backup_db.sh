#!/bin/bash
# xueqiusub 每日数据库备份：容器内 sqlite backup API（WAL 安全）→ 宿主机 backups/，保留 14 天
set -u
VOL=/var/lib/docker/volumes/xueqiusub_app-data/_data
DEST=/root/xueqiusub/backups
TS=$(date +%Y%m%d_%H%M%S)
mkdir -p "$DEST"

if ! docker exec -i xueqiusub-worker python - <<'EOF'
import sqlite3
src = sqlite3.connect('/app/data/xueqiu.db')
dst = sqlite3.connect('/app/data/.backup_tmp.db')
with dst:
    src.backup(dst)
src.close(); dst.close()
EOF
then
  echo "$(date '+%F %T') backup FAILED (docker exec 或 sqlite backup 失败)"
  exit 1
fi
mv "$VOL/.backup_tmp.db" "$DEST/xueqiu.db.bak_$TS"
find "$DEST" -name 'xueqiu.db.bak_*' -mtime +14 -delete
echo "$(date '+%F %T') backup OK: xueqiu.db.bak_$TS ($(du -h "$DEST/xueqiu.db.bak_$TS" | cut -f1))"
