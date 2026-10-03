package dev.vidtheque.app.auth

import dev.vidtheque.app.BuildConfig
import javax.inject.Inject
import javax.inject.Singleton

/**
 * The one instance this build signs in to, and the client identity it holds there.
 * The server publishes the client document and the callback (mcp auth/android.py).
 */
@Singleton
class Instance(val base: String) {
    @Inject constructor() : this(BuildConfig.INSTANCE)

    val host: String get() = base.substringAfter("://").substringBefore('/')
    val clientId: String get() = "$base/auth/android/client.json"
    val callbackPath: String get() = "/auth/android/callback"
    val redirectUri: String get() = "$base$callbackPath"
    val scope: String get() = "vidtheque:read vidtheque:write offline_access"
}
