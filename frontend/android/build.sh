#!/usr/bin/env bash
# 合同审查 App —— 安卓 WebView 壳构建脚本（无需 Gradle / Android Studio）
# 只依赖：JDK 17、Android SDK build-tools + platform、python
# 路径可由环境变量覆盖：ANDROID_SDK / JAVA_HOME / CR_OUT
set -e

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APK_NAME="${APK_NAME:-contract-review-v1.5.apk}"
VER_CODE="${VERSION_CODE:-6}"
VER_NAME="${VERSION_NAME:-1.5}"

SDK="${ANDROID_SDK:-${ANDROID_HOME:-C:/dev/android-sdk}}"
BT_DIR="$SDK/build-tools"
BT_VER="$(ls -1 "$BT_DIR" 2>/dev/null | sort -V | tail -1)"
[ -z "$BT_VER" ] && { echo "!! 找不到 build-tools: $BT_DIR"; exit 1; }
BT="$BT_DIR/$BT_VER"

pick() { for c in "$@"; do if [ -e "$c" ] || command -v "$c" >/dev/null 2>&1; then echo "$c"; return 0; fi; done; return 1; }
AAPT2="$(pick "$BT/aapt2.exe" "$BT/aapt2")"
ZIPALIGN="$(pick "$BT/zipalign.exe" "$BT/zipalign")"
APKSIGNER="$(pick "$BT/apksigner.bat" "$BT/apksigner")"
D8="$(pick "$BT/d8.bat" "$BT/d8")"
PLAT=""
for pv in 36 35 34; do [ -f "$SDK/platforms/android-$pv/android.jar" ] && { PLAT="$SDK/platforms/android-$pv/android.jar"; break; }; done
[ -z "$PLAT" ] && { echo "!! 找不到 android.jar"; exit 1; }

JH="${JAVA_HOME:-}"
JAVAC="$JH/bin/javac"; KEYTOOL="$JH/bin/keytool"
[ -x "$JAVAC" ] || JAVAC="$(command -v javac)"
[ -x "$KEYTOOL" ] || KEYTOOL="$(command -v keytool)"
[ -n "$JAVAC" ] || { echo "!! javac not found; set JAVA_HOME or add it to PATH"; exit 1; }
[ -n "$KEYTOOL" ] || { echo "!! keytool not found; set JAVA_HOME or add it to PATH"; exit 1; }
PY="$(pick python python3 py)"

OUT="${CR_OUT:-$HERE/build}"
mkdir -p "$HERE/dist"
rm -rf "$OUT"; mkdir -p "$OUT/gen" "$OUT/classes" "$OUT/dex"

echo "==> SDK      $BT"
echo "==> PLATFORM $PLAT"
echo "==> JDK      $JAVAC"
echo "==> 产物     $HERE/dist/$APK_NAME (v$VER_NAME / $VER_CODE)"

echo "==> 1/6 aapt2 compile"
"$AAPT2" compile --dir "$HERE/res" -o "$OUT/res.zip"

echo "==> 2/6 aapt2 link"
"$AAPT2" link -o "$OUT/app-unsigned.apk" -I "$PLAT" \
  --manifest "$HERE/AndroidManifest.xml" -A "$HERE/assets" \
  --java "$OUT/gen" --min-sdk-version 24 --target-sdk-version 35 \
  --version-code "$VER_CODE" --version-name "$VER_NAME" --auto-add-overlay \
  "$OUT/res.zip"

echo "==> 3/6 javac"
SRCS=$(find "$HERE/java" "$OUT/gen" -name "*.java" | tr '\n' ' ')
"$JAVAC" -encoding UTF-8 -source 8 -target 8 -bootclasspath "$PLAT" \
  -classpath "$PLAT" -d "$OUT/classes" $SRCS > "$OUT/javac.log" 2>&1 \
  || { echo "!! javac 失败"; cat "$OUT/javac.log"; exit 1; }
echo "  编译通过: $(find "$OUT/classes" -name '*.class' | wc -l) classes"

echo "==> 4/6 d8"
CLASSES=$(find "$OUT/classes" -name "*.class" | tr '\n' ' ')
"$D8" --release --min-api 24 --lib "$PLAT" --output "$OUT/dex" $CLASSES > /dev/null

echo "==> 5/6 merge dex + zipalign"
"$PY" - "$OUT/app-unsigned.apk" "$OUT/dex/classes.dex" "$OUT/app-dex.apk" <<'PYEOF'
import sys, zipfile, shutil
src, dex, dst = sys.argv[1], sys.argv[2], sys.argv[3]
shutil.copyfile(src, dst)
with zipfile.ZipFile(dst, 'a', zipfile.ZIP_DEFLATED) as z:
    z.write(dex, 'classes.dex')
PYEOF
"$ZIPALIGN" -f -p 4 "$OUT/app-dex.apk" "$OUT/app-aligned.apk"

echo "==> 6/6 sign"
KS="${ANDROID_KEYSTORE:-contract-review.keystore}"
case "$KS" in
  /*|[A-Za-z]:/*) ;;
  *) KS="$HERE/$KS" ;;
esac
KS_PASS="${ANDROID_KS_PASS:-}"
KEY_PASS="${ANDROID_KEY_PASS:-$KS_PASS}"
ALIAS="${ANDROID_KEY_ALIAS:-contractreview}"
[ -n "$KS_PASS" ] || { echo "!! ANDROID_KS_PASS is required (see .env.example)"; exit 1; }
if [ ! -f "$KS" ]; then
  "$KEYTOOL" -genkeypair -keystore "$KS" -storepass "$KS_PASS" -keypass "$KEY_PASS" \
    -alias "$ALIAS" -keyalg RSA -keysize 2048 -validity 10000 \
    -dname "CN=Contract Review, OU=Personal, O=ContractReview, L=Changsha, ST=Hunan, C=CN" >/dev/null 2>&1
  echo "  已生成签名密钥（请妥善备份 $KS，丢失后无法覆盖安装）"
fi
"$APKSIGNER" sign --ks "$KS" --ks-pass "pass:$KS_PASS" --key-pass "pass:$KEY_PASS" \
  --v1-signing-enabled true --v2-signing-enabled true \
  --out "$HERE/dist/$APK_NAME" "$OUT/app-aligned.apk"

"$APKSIGNER" verify --print-certs "$HERE/dist/$APK_NAME" | head -4
echo "==> 完成："
ls -lh "$HERE/dist/$APK_NAME"
