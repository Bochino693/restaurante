#!/bin/sh
# Compila o APK sem Gradle. Requer (Debian/Ubuntu):
#   apt-get install aapt apksigner zipalign dalvik-exchange default-jdk
# e ANDROID_JAR apontando para um android.jar API 24+ (veja o README).
set -e
cd "$(dirname "$0")"
JAR=${ANDROID_JAR:-/usr/lib/android-sdk/platforms/android-23/android.jar}  # precisa ser API 24+ (directBootAware)
rm -rf build && mkdir -p build/gen build/classes build/dex
aapt package -f -m -J build/gen -M AndroidManifest.xml -S res -I "$JAR"
javac -encoding UTF-8 -nowarn -Xlint:-options -source 8 -target 8 -cp "$JAR" -d build/classes \
    build/gen/com/iniciorapido/*.java src/com/iniciorapido/*.java
dalvik-exchange --dex --output=build/dex/classes.dex build/classes
aapt package -f -M AndroidManifest.xml -S res -I "$JAR" -F build/unaligned.apk
(cd build/dex && aapt add ../unaligned.apk classes.dex >/dev/null)
zipalign -f 4 build/unaligned.apk build/aligned.apk
[ -f inicio-rapido.keystore ] || keytool -genkeypair -keystore inicio-rapido.keystore \
    -storepass iniciorapido -keypass iniciorapido -alias app -keyalg RSA -sigalg SHA256withRSA -keysize 2048 \
    -validity 20000 -dname "CN=Inicio Rapido"
apksigner sign --ks inicio-rapido.keystore --ks-pass pass:iniciorapido --key-pass pass:iniciorapido \
    --v1-signing-enabled true --v2-signing-enabled true --out inicio-rapido.apk build/aligned.apk
rm -f inicio-rapido.apk.idsig
apksigner verify --min-sdk-version 16 --print-certs inicio-rapido.apk | head -1
ls -l inicio-rapido.apk
