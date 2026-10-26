#!/usr/bin/env bash
# Build TV Home without Gradle: aapt -> javac -> d8 (or dx) -> zipalign -> apksigner.
#
# With an Android SDK installed, this just works:   ./build.sh
#   (uses $ANDROID_HOME/platforms/android-33+/android.jar and the newest build-tools)
# Without one, point it at the pieces:
#   ANDROID_JAR=android-all.jar FRAMEWORK_RES=framework-res.apk ./build.sh
#
# Output: build/TVHome.apk, signed with a local key in ~/.tvhome/ (created on first build).
# Rebuilding with a different key means the TV needs `adb uninstall dev.tvhome` before installing.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${OUT:-$HERE/build}
SDK=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-$HOME/Android/Sdk}}

newest() { ls -d $1 2>/dev/null | sort -V | tail -1; }
tool() { # tool NAME: build-tools first, then PATH
  local bt; bt=$(newest "$SDK/build-tools/*")
  if [ -n "$bt" ] && [ -x "$bt/$1" ]; then echo "$bt/$1"; else command -v "$1" || true; fi
}

if [ -z "${ANDROID_JAR:-}" ]; then
  for api in 36 35 34 33; do
    if [ -f "$SDK/platforms/android-$api/android.jar" ]; then ANDROID_JAR=$SDK/platforms/android-$api/android.jar; break; fi
  done
fi
[ -n "${ANDROID_JAR:-}" ] && [ -f "$ANDROID_JAR" ] || { echo "Set ANDROID_JAR (platform 33+ android.jar)" >&2; exit 1; }
FRAMEWORK_RES=${FRAMEWORK_RES:-$ANDROID_JAR}   # an SDK android.jar carries the framework resources

AAPT=${AAPT:-$(tool aapt)}
ZIPALIGN=${ZIPALIGN:-$(tool zipalign)}
APKSIGNER=${APKSIGNER:-$(tool apksigner)}
D8=${D8:-$(tool d8)}
DX=${DX:-$(command -v dalvik-exchange || command -v dx || true)}
for t in AAPT ZIPALIGN APKSIGNER; do [ -n "${!t}" ] || { echo "Missing $t (install Android build-tools)" >&2; exit 1; }; done
[ -n "$D8" ] || [ -n "$DX" ] || { echo "Missing d8 or dx" >&2; exit 1; }

KEYSTORE=${KEYSTORE:-$HOME/.tvhome/signing.jks}
KEYPASS=${KEYPASS:-tvhome-local}
if [ ! -f "$KEYSTORE" ]; then
  mkdir -p "$(dirname "$KEYSTORE")"
  keytool -genkeypair -noprompt -keystore "$KEYSTORE" -storepass "$KEYPASS" -keypass "$KEYPASS" \
    -alias tvhome -keyalg RSA -keysize 2048 -validity 10000 -dname "CN=TV Home (local build)" >/dev/null
  echo "Created signing key $KEYSTORE"
fi

rm -rf "$OUT"
mkdir -p "$OUT/gen" "$OUT/classes"

echo "==> resources"
"$AAPT" package -f -m --auto-add-overlay \
  -M "$HERE/AndroidManifest.xml" -S "$HERE/res" -A "$HERE/assets" \
  -I "$FRAMEWORK_RES" -J "$OUT/gen" -F "$OUT/unaligned.apk" \
  --min-sdk-version 21 --target-sdk-version 33 -0 arsc -0 jpg -0 png

echo "==> java"
find "$HERE/src" "$OUT/gen" -name '*.java' > "$OUT/sources.txt"
# An SDK android.jar includes java.*; Robolectric's android-all jar does not, so it goes on the
# classpath next to the JDK's Java 8 API instead.
if (unzip -l "$ANDROID_JAR" java/lang/Object.class || jar tf "$ANDROID_JAR" | grep -qx java/lang/Object.class) >/dev/null 2>&1; then
  JAVAC_PLATFORM=(-source 8 -target 8 -bootclasspath "$ANDROID_JAR")
else
  JAVAC_PLATFORM=(--release 8 -classpath "$ANDROID_JAR")
fi
javac "${JAVAC_PLATFORM[@]}" -encoding UTF-8 -Xlint:-options -Xlint:-deprecation \
  -d "$OUT/classes" @"$OUT/sources.txt"

echo "==> dex"
if [ -n "$D8" ]; then
  "$D8" --release --min-api 21 --lib "$ANDROID_JAR" --output "$OUT" $(find "$OUT/classes" -name '*.class')
else
  "$DX" --dex --min-sdk-version=21 --output="$OUT/classes.dex" "$OUT/classes"
fi
(cd "$OUT" && "$AAPT" add unaligned.apk classes.dex >/dev/null)

echo "==> align + sign"
"$ZIPALIGN" -f -p 4 "$OUT/unaligned.apk" "$OUT/aligned.apk"
"$APKSIGNER" sign --ks "$KEYSTORE" --ks-pass "pass:$KEYPASS" --key-pass "pass:$KEYPASS" \
  --ks-key-alias tvhome --min-sdk-version 21 --out "$OUT/TVHome.apk" "$OUT/aligned.apk"
"$APKSIGNER" verify "$OUT/TVHome.apk"
rm -f "$OUT/unaligned.apk" "$OUT/aligned.apk" "$OUT/TVHome.apk.idsig"
echo "Built $OUT/TVHome.apk ($(du -h "$OUT/TVHome.apk" | cut -f1))"
