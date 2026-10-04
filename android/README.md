# vidtheque for Android

The companion's phone app (`docs/design/companion.md` §6): Feed, Video and
Profile over the feed endpoints (`docs/design/dashboard.md` §25), signed in
to one instance through its OAuth server. Kotlin, Jetpack Compose, Material 3
Expressive in Roboto Flex on a gold-seeded dark scheme (DESIGN.md, "The
Android app").

## Build

JDK 21 and an Android SDK with platform 37, then:

```bash
./gradlew assembleRelease
```

The APK lands in `app/build/outputs/apk/release/`. Sign-in asks for the
instance's address, prefilled with `vidtheque.instance` from
`gradle.properties` (`-Pvidtheque.instance=` leaves it empty). Push works on
one instance only, `vidtheque.pushInstance`: FCM tokens belong to one Firebase
project, and only the server holding its key can send to them.

Screenshots render on the JVM through Roborazzi, no emulator needed:
`./gradlew recordRoborazziDebug` writes `app/screenshots/`, and
`verifyRoborazziDebug` fails when a screen drifts from them. Emulator
captures for PR bodies go in `screenshots/`.

## CI and releases

`.github/workflows/android.yml` builds on hosted runners, then installs the
APK on an emulator and runs the Maestro flows in `maestro/`. A tag
`android-v0.1.0` runs `android-release.yml`, which attaches
`vidtheque-0.1.0.apk` to a GitHub release.

Builds sign with the key in the repository secrets `ANDROID_KEYSTORE_B64`,
`ANDROID_KEYSTORE_PASSWORD` and `ANDROID_KEY_ALIAS`, so each one installs over
the last. Without them (a fork, a local build) the debug key signs. No key is
ever committed.
