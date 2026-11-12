#!/usr/bin/env bash
# 开发后端公网隧道（TestFlight 真机测试用，ADR：企业网 AP 隔离绕行）。
# 用法：bash scripts/dev_tunnel.sh start|status|url
# ⚠️ 临时隧道（trycloudflare.com）重启后 URL 变化：重跑 start 后需把新 URL
#    更新进 apps/ios/.../APIClient.swift defaultBaseURLString 并重新发包，
#    或在 App 设置页手填。
set -euo pipefail
case "${1:-status}" in
  start)
    nohup /opt/homebrew/bin/cloudflared tunnel --url http://localhost:8080 --no-autoupdate \
      > /tmp/tunnel_log.txt 2>&1 & disown
    sleep 8
    URL=$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" /tmp/tunnel_log.txt | head -1 || true)
    echo "隧道 URL：${URL:-未就绪，稍后 cat /tmp/tunnel_log.txt}"
    ;;
  status)
    pgrep -fl "cloudflared tunnel" >/dev/null && echo "运行中" || echo "未运行（bash scripts/dev_tunnel.sh start）"
    grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" /tmp/tunnel_log.txt 2>/dev/null | head -1
    ;;
esac
