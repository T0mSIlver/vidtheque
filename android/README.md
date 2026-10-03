# vidtheque for Android

The companion's phone app (`docs/design/companion.md` §6): Feed, Video and
Profile over the feed endpoints (`docs/design/dashboard.md` §25), signed in
to one instance through its OAuth server. Kotlin, Jetpack Compose, Material 3
themed with DESIGN.md's tokens.

## Build

JDK 21 and an Android SDK with platform 37, then:

```bash
./gradlew assembleRelease
```

The APK lands in `app/build/outputs/apk/release/`. The instance is fixed at
build time: `-Pvidtheque.instance=https://your.host` overrides the default
in `gradle.properties`.

Screenshots render on the JVM through Roborazzi, no emulator needed:
`./gradlew recordRoborazziDebug` writes `app/screenshots/`, and
`verifyRoborazziDebug` fails when a screen drifts from them.

## CI and releases

`.github/workflows/android.yml` builds on hosted runners, then installs the
APK on an emulator and runs the Maestro flows in `maestro/`. A tag
`android-v0.1.0` attaches `vidtheque-0.1.0.apk` to a GitHub release.

Builds sign with the key in the repository secrets `ANDROID_KEYSTORE_B64`,
`ANDROID_KEYSTORE_PASSWORD` and `ANDROID_KEY_ALIAS`, so each one installs over
the last. Without them (a fork, a local build) the debug key signs. No key is
ever committed.
