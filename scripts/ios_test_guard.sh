#!/usr/bin/env bash
# iOS 测试守卫：本机 xcodebuild 偶发挂死（`waitForBuildWithBuildLog` /
# `PruneExplicitPrecompiledModules` / `installApplication` / 交互阶段等）——
# 进程还在但长时间零输出，测试实际不会开始或收尾。
#
# 本脚本按「输出停滞」判定挂死：停滞 ≥ STALL_SECONDS 就杀掉、清构建缓存与残留
# 进程、重试，最多 MAX_ATTEMPTS 次。**真正的测试失败不重试**（避免掩盖产品缺陷，
# 只把失败摘要打出来并退出 1）。
#
# 用法：
#   DEST='platform=iOS Simulator,name=GBT-E2E' scripts/ios_test_guard.sh
#   ONLY_TESTING='GlobalTravelOSTests' DEST=... scripts/ios_test_guard.sh
#
# 退出码：0 通过 / 1 测试失败 / 3 连续挂死放弃
set -uo pipefail

DEST="${DEST:-${1:-}}"
[ -n "$DEST" ] || { echo "[guard] 缺少 destination（DEST=...）" >&2; exit 64; }
ONLY_TESTING="${ONLY_TESTING:-}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-3}"
STALL_SECONDS="${STALL_SECONDS:-240}"
XCODEBUILD_ACTION="${XCODEBUILD_ACTION:-test}"
RESULT_BUNDLE_PATH="${RESULT_BUNDLE_PATH:-}"
LOG_DIR="${LOG_DIR:-/tmp}"
case "$XCODEBUILD_ACTION" in
  test|test-without-building) ;;
  *) echo "[guard] 不支持的 XCODEBUILD_ACTION：$XCODEBUILD_ACTION" >&2; exit 64 ;;
esac
mkdir -p "$LOG_DIR"
if [ -n "$RESULT_BUNDLE_PATH" ]; then
  rm -rf "$RESULT_BUNDLE_PATH"
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR/../apps/ios"
DERIVED="$HOME/Library/Developer/Xcode/DerivedData"

clean_build_state() {
  pkill -9 -f "xcodebuild.*GlobalTravelOS" 2>/dev/null
  pkill -9 -f "GlobalTravelOSUITests-Runner" 2>/dev/null
  rm -rf "$DERIVED"/GlobalTravelOS-*/Build/Intermediates.noindex/XCBuildData \
         "$DERIVED"/GlobalTravelOS-*/Build/Intermediates.noindex/ExplicitPrecompiledModules \
         "$DERIVED"/GlobalTravelOS-*/Build/Intermediates.noindex/SwiftExplicitPrecompiledModules 2>/dev/null
}

# 返回 0=通过 / 1=真失败 / 2=挂死
run_attempt() {
  local log="$1" pid now last
  local -a xcode_args
  xcode_args=(xcodebuild "$XCODEBUILD_ACTION" -project GlobalTravelOS.xcodeproj
    -scheme GlobalTravelOS -destination "$DEST" -parallel-testing-enabled NO)
  if [ -n "$ONLY_TESTING" ]; then
    xcode_args+=("-only-testing:$ONLY_TESTING")
  fi
  if [ -n "$RESULT_BUNDLE_PATH" ]; then
    xcode_args+=(-resultBundlePath "$RESULT_BUNDLE_PATH")
  fi
  ( cd "$PROJECT_DIR" && "${xcode_args[@]}" >"$log" 2>&1 ) &
  pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    sleep 10
    [ -f "$log" ] || continue
    now=$(date +%s)
    last=$(stat -f %m "$log" 2>/dev/null || echo "$now")
    if [ $((now - last)) -ge "$STALL_SECONDS" ]; then
      echo "[guard] 输出停滞 $((now - last))s，判定挂死：清理构建缓存与残留进程" >&2
      clean_build_state
      wait "$pid" 2>/dev/null
      return 2
    fi
  done
  wait "$pid"
  local status=$?
  if [ "$status" -ne 0 ]; then
    return 1
  fi
  if grep -q "TEST SUCCEEDED" "$log" && grep -Eq "Executed [1-9][0-9]* tests" "$log"; then
    return 0
  fi
  # xcodebuild 退出 0 但没跑任何用例，或测试摘要缺失——按失败处理，防止空跑误报绿灯。
  return 1
}

attempt=1
while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
  log="$LOG_DIR/ios-test-attempt-${attempt}-$(date +%s).log"
  echo "[guard] 第 ${attempt}/${MAX_ATTEMPTS} 次 xcodebuild test（日志：${log}）"
  run_attempt "$log"
  status=$?
  if [ "$status" -eq 0 ]; then
    grep -E "Executed [0-9]+ tests" "$log" | tail -1 | sed 's/^/    /'
    echo "[guard] 通过"
    exit 0
  fi
  if [ "$status" -eq 2 ]; then
    attempt=$((attempt + 1))
    continue
  fi
  echo "[guard] 测试失败（非挂死，不重试）：" >&2
  grep -E "error:|Test Case .* failed|Executed [0-9]+ tests" "$log" | tail -20 >&2
  exit 1
done
echo "[guard] 连续 ${MAX_ATTEMPTS} 次挂死，放弃。检查是否有其他项目的 xcodebuild/模拟器占用：" >&2
pgrep -fl "xcodebuild" | head -5 >&2 || true
exit 3
