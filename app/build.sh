#!/bin/bash
# Build wf-composer.apk without Gradle: aapt2 -> javac -> d8 -> zipalign -> apksigner
set -e
SDK=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}
if [ -z "$SDK" ]; then
    for d in "$HOME/Android/Sdk" "$HOME/android-sdk"; do
        [ -d "$d" ] && SDK=$d && break
    done
fi
if [ -z "$SDK" ] || [ ! -d "$SDK" ]; then
    echo "Android SDK not found — set ANDROID_HOME (e.g. export ANDROID_HOME=~/Android/Sdk)" >&2
    exit 1
fi
BT=$(ls -d "$SDK"/build-tools/* 2>/dev/null | sort -V | tail -1)
AJAR=$(ls -d "$SDK"/platforms/android-*/android.jar 2>/dev/null | sort -V | tail -1)
[ -n "$BT" ] && [ -n "$AJAR" ] || { echo "SDK incomplete — need build-tools/ and platforms/android-*/" >&2; exit 1; }
for tool in javac zip keytool; do
    command -v "$tool" >/dev/null || { echo "missing: $tool" >&2; exit 1; }
done
ROOT=$(cd "$(dirname "$0")" && pwd)
B="$ROOT/build"
rm -rf "$B"; mkdir -p "$B/gen" "$B/classes" "$B/dex"

echo "[1/5] aapt2 compile+link"
mkdir -p "$ROOT/assets"
cp "$ROOT/../web/pair.html" "$ROOT/assets/" 2>/dev/null || true
"$BT/aapt2" compile --dir "$ROOT/res" -o "$B/res.zip"
"$BT/aapt2" link -o "$B/app.apk" -I "$AJAR" \
    --manifest "$ROOT/AndroidManifest.xml" "$B/res.zip" --java "$B/gen" \
    -A "$ROOT/assets"

echo "[2/5] javac"
javac --release 8 -cp "$AJAR" -d "$B/classes" \
    "$B/gen/com/wf/composer/R.java" "$ROOT/src/com/wf/composer/MainActivity.java"

echo "[3/5] d8"
"$BT/d8" --lib "$AJAR" --min-api 26 --output "$B/dex" \
    $(find "$B/classes" -name '*.class')
(cd "$B/dex" && zip -qj "$B/app.apk" classes.dex)

echo "[4/5] zipalign"
"$BT/zipalign" -f -p 4 "$B/app.apk" "$B/app-aligned.apk"

echo "[5/5] sign"
KS="$HOME/.android/debug.keystore"
if [ ! -f "$KS" ]; then
    keytool -genkeypair -v -keystore "$KS" -storepass android -keypass android \
        -alias androiddebugkey -keyalg RSA -keysize 2048 -validity 10950 \
        -dname "CN=Android Debug,O=Android,C=US" -noprompt
fi
"$BT/apksigner" sign --ks "$KS" --ks-pass pass:android --key-pass pass:android \
    --out "$ROOT/wf-composer.apk" "$B/app-aligned.apk"

echo "built: $ROOT/wf-composer.apk"
