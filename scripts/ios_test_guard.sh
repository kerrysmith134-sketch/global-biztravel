#!/usr/bin/env bash
# iOS 测试守卫：本机 xcodebuild 偶发挂死（`waitForBuildWithBuildLog` /
# `PruneExplicitPrecompiledModules` / `installApplication` / 交互阶段等）——
# 进程还在但长时间零输出，测试实际不会开始或收尾。
#
# UI Test Runner 启动应用、安装 runner 和执行长链式交互时可能长时间不写 xcodebuild 日志。
# 不应以短静默阈值误杀仍在执行的测试；每条 UI 测试另有 XCTest executionTimeAllowance。
# 超过 STALL_SECONDS 仍无输出才视为基础设施挂死。真正的断言失败不重试。
#
# 用法：
#   DEST='platform=iOS Simulator,name=GBT-E2E' scripts/ios_test_guard.sh
#   ONLY_TESTING='GlobalTravelOSTests' DEST=... scripts/ios_test_guard.sh
#
# 退出码：0 通过 / 1 测试失败 / 3 连续挂死放弃
set -uo pipefail

DEST="${DEST:-${1:-}}"
[ -n "$DEST" ] || { echo "[guard] 缺少 destination（DEST=... 或首个位置参数）" >&2; exit 64; }

# Resolve a missing named simulator here rather than in Makefile. This script is
# always invoked with bash, so the Make recipe does not depend on executable bits
# or Make/shell dollar escaping.
if [[ "$DEST" == *"name="* ]]; then
  requested_name="${DEST##*name=}"
  if ! xcrun simctl list devices available | grep -Fq "$requested_name"; then
    fallback="$(xcrun simctl list devices available \
      | sed -n 's/^ *\(iPhone [^(]*\) (.*/\1/p' \
      | head -1 | sed 's/ *$//')"
    if [ -z "$fallback" ]; then
      echo "[guard] 没有可用的 iPhone 模拟器" >&2
      exit 1
    fi
    echo "[guard] 目标模拟器 '$requested_name' 不存在，回退到 '$fallback'" >&2
    DEST="platform=iOS Simulator,name=$fallback"
  fi
fi
ONLY_TESTING="${ONLY_TESTING:-}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-3}"
STALL_SECONDS="${STALL_SECONDS:-900}"
RESULT_BUNDLE_BASE="${RESULT_BUNDLE_BASE:-}"
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


reset_simulator() {
  local simulator_id=""
  if [[ "$DEST" =~ id=([^,]+) ]]; then
    simulator_id="${BASH_REMATCH[1]}"
  fi
  [ -n "$simulator_id" ] || return 0
  xcrun simctl shutdown "$simulator_id" 2>/dev/null || true
  xcrun simctl boot "$simulator_id" 2>/dev/null || true
  xcrun simctl bootstatus "$simulator_id" -b
}

# 返回 0=通过 / 1=真失败 / 2=挂死
run_attempt() {
  local log="$1" attempt_number="$2" pid now last
  local result_bundle_args=()
  if [ -n "$RESULT_BUNDLE_BASE" ]; then
    local result_bundle="${RESULT_BUNDLE_BASE}.attempt-${attempt_number}.xcresult"
    rm -rf "$result_bundle"
    result_bundle_args=(-resultBundlePath "$result_bundle")
  fi
  ( cd "$PROJECT_DIR" && xcodebuild test -project GlobalTravelOS.xcodeproj \
      -scheme GlobalTravelOS -destination "$DEST" \
      -parallel-testing-enabled NO -destination-timeout 120 \
      "${result_bundle_args[@]}" \
      ${ONLY_TESTING:+-only-testing:"$ONLY_TESTING"} \
      >"$log" 2>&1 ) &
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
    # XCTest can terminate its runner before test execution starts. This is an infrastructure
    # bootstrap failure, not a product assertion failure, and is safe to retry once.
    if grep -Eqi \
      'Early unexpected exit, operation never finished bootstrapping|Test crashed with signal kill before starting test execution' \
      "$log"; then
      echo "[guard] 检测到 XCTest Runner 启动失败；清理构建状态并重启模拟器后重试" >&2
      clean_build_state
      reset_simulator
      return 2
    fi
    return 1
  fi
  if grep -q "TEST SUCCEEDED" "$log"; then
    return 0
  fi
  # xcodebuild 退出 0 但没跑任何用例（-only-testing 标识不符时会这样）——按失败处理。
  return 1
}

attempt=1
while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
  log="/tmp/ios-test-attempt-${attempt}-$(date +%s).log"
  echo "[guard] 第 ${attempt}/${MAX_ATTEMPTS} 次 xcodebuild test（日志：${log}）"
  run_attempt "$log" "$attempt"
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
  # Match actual compiler diagnostics (file:line:column: error:) rather than source-code
  # arguments such as "error: message", which previously polluted the report.
  grep -E '^/.*:[0-9]+:[0-9]+: error:|^error:|Test Case .* failed|Executed [0-9]+ tests|Early unexpected exit|Test crashed with signal kill before starting test execution' "$log" | tail -30 >&2
  exit 1
done
echo "[guard] 连续 ${MAX_ATTEMPTS} 次挂死，放弃。检查是否有其他项目的 xcodebuild/模拟器占用：" >&2
pgrep -fl "xcodebuild" | head -5 >&2 || true
exit 3
