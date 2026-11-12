#!/usr/bin/env bash
# iOS 构建 + TestFlight 发布流水线（参照 tu-zhang docs/TESTFLIGHT_PLAN.md §1.4）。
#
# 用法：
#   bash scripts/ios_release.sh                  # 归档 + 校验（归档入 Xcode Archives，供 Organizer）
#   bash scripts/ios_release.sh --export         # + 导出 App Store IPA（需 Xcode 账号会话）
#   bash scripts/ios_release.sh --upload         # + 上传（优先 API key，其次 Organizer 提示）
#   BUILD_NUMBER=3 bash scripts/ios_release.sh   # 覆盖 build 号（默认自动 +1）
#
# 前置（一次性，GUI）：Xcode → Settings → Accounts 登录 XC9LAN9MW9（CAIGANG CHU）
# 的 Apple ID；App Store Connect「我的 App」需已创建 com.anari.globaltravelos 的记录。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IOS="$ROOT/apps/ios"
TEAM="XC9LAN9MW9"
BUNDLE="com.anari.globaltravelos"
MODE="${1:-}"

cd "$IOS"

echo "== 版本号 =="
CURRENT_BUILD=$(grep -m1 "CURRENT_PROJECT_VERSION" GlobalTravelOS.xcodeproj/project.pbxproj | grep -oE "[0-9]+" | head -1)
MARKETING=$(grep -m1 "MARKETING_VERSION" GlobalTravelOS.xcodeproj/project.pbxproj | grep -oE "[0-9.]+" | head -1)
BUILD="${BUILD_NUMBER:-$((CURRENT_BUILD + 1))}"
if [[ "$BUILD" != "$CURRENT_BUILD" ]]; then
  echo "build: ${CURRENT_BUILD} → ${BUILD}（${MARKETING}(${BUILD})）"
  sed -i '' "s/CURRENT_PROJECT_VERSION = $CURRENT_BUILD;/CURRENT_PROJECT_VERSION = $BUILD;/g" \
    GlobalTravelOS.xcodeproj/project.pbxproj
  (cd "$ROOT" && git add apps/ios/GlobalTravelOS.xcodeproj/project.pbxproj) || true
fi
echo "版本：${MARKETING}(${BUILD})"

echo "== 归档（Release，generic/iOS）=="
ARCHIVE="build/GlobalTravelOS-$MARKETING-$BUILD.xcarchive"
xcodebuild -project GlobalTravelOS.xcodeproj -scheme GlobalTravelOS \
  -configuration Release -destination 'generic/platform=iOS' \
  -archivePath "$ARCHIVE" archive -quiet
echo "ARCHIVE SUCCEEDED: $ARCHIVE"

echo "== 校验（tu-zhang §1.4 第 4 步：plist 必须展开）=="
APP_PLIST="$ARCHIVE/Info.plist"
VERSION=$(/usr/libexec/PlistBuddy -c "Print :ApplicationProperties:CFBundleShortVersionString" "$APP_PLIST")
BUILD_NUM=$(/usr/libexec/PlistBuddy -c "Print :ApplicationProperties:CFBundleVersion" "$APP_PLIST")
TEAM_ID=$(/usr/libexec/PlistBuddy -c "Print :ApplicationProperties:Team" "$APP_PLIST")
[[ "$VERSION" == "$MARKETING" && "$BUILD_NUM" == "$BUILD" ]] || {
  echo "FAIL: 版本号未展开 $VERSION($BUILD_NUM)"; exit 1; }
[[ "$TEAM_ID" == "$TEAM" ]] || { echo "FAIL: 签名团队 $TEAM_ID ≠ $TEAM"; exit 1; }
codesign --verify --deep --strict "$ARCHIVE/Products/Applications/GlobalTravelOS.app"
echo "校验通过：$VERSION($BUILD_NUM)，Team=$TEAM_ID"

# 放进 Xcode 默认归档目录，Organizer 直接可见
ARCHIVE_DATE_DIR="$HOME/Library/Developer/Xcode/Archives/$(date +%Y-%m-%d)"
mkdir -p "$ARCHIVE_DATE_DIR"
cp -R "$ARCHIVE" "$ARCHIVE_DATE_DIR/"
echo "归档已放入 $ARCHIVE_DATE_DIR/（Organizer 可见）"

if [[ "$MODE" != "--export" && "$MODE" != "--upload" ]]; then
  echo "完成。上传：Organizer → Distribute App（或重跑 --export/--upload）"
  exit 0
fi

echo "== 导出 App Store IPA（需要 Xcode 账号会话；No Accounts = 先在 Xcode GUI 登录）=="
rm -rf build/export
xcodebuild -exportArchive -archivePath "$ARCHIVE" -exportOptionsPlist ExportOptions.plist \
  -exportPath build/export -allowProvisioningUpdates -quiet
IPA=$(find build/export -name "*.ipa" | head -1)
[[ -n "$IPA" ]] || { echo "FAIL: 未找到 IPA"; exit 1; }
echo "IPA: $IPA"

if [[ "$MODE" != "--upload" ]]; then
  echo "完成。上传：重跑 --upload（exportArchive destination=upload）"
  exit 0
fi

echo "== 上传（tu-zhang 同款：ExportOptions destination=upload，Xcode 会话认证，免 API key）=="
xcodebuild -exportArchive -archivePath "$ARCHIVE" -exportOptionsPlist ExportOptions.plist \
  -allowProvisioningUpdates -quiet
echo "上传完成：ASC 处理 10–30 分钟后 TestFlight 可见。"
