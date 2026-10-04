vidtheque for Android tells you which new videos from the builders you follow are worth your time. Your agent watched them: each one gets a verdict, a reason and the moments to jump to.

The app needs your own vidtheque instance with sign-in on (`VIDTHEQUE_AUTH=oauth`, see the [quickstart](https://github.com/T0mSIlver/vidtheque#quickstart)). It has no account of its own and talks only to the instance you sign in to. An instance too old for the app says so at sign-in.

Notifications work only on the developer's instance, because only that server holds the key to the Firebase project the app uses. On yours, the app works without them.

## Install

1. On the phone, download `vidtheque-{version}.apk` below and open it.
2. The first time, Android asks you to allow installs from your browser or file manager. Allow it for this install.
3. Open vidtheque, enter your instance's address and sign in.

Each release installs over the previous one and keeps you signed in.

## Check the APK

Every release is signed with the same key. Its certificate's SHA-256 is:

```
{sha256}
```

To compare, run `apksigner verify --print-certs vidtheque-{version}.apk` from the Android SDK build tools and read the `certificate SHA-256 digest` line. Android also refuses an update signed with another key.
