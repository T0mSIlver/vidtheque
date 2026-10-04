package dev.vidtheque.app.auth

import dev.vidtheque.app.BuildConfig
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import javax.inject.Inject
import javax.inject.Singleton

/**
 * The instance the app is signed in to, chosen at sign-in, and the client identity
 * it holds there. The server publishes the client document (mcp auth/android.py).
 */
@Singleton
class Instance(val default: String, val devToken: String = "") {
    @Inject constructor() : this(BuildConfig.INSTANCE, BuildConfig.DEV_TOKEN)

    /** Set from the stored session at launch, and by each sign-in. */
    @Volatile var base: String = default

    val host: String get() = hostOf(base)

    companion object {
        /** A private-use scheme (RFC 8252 §7.1): Auth Tab hands it back to this app, whatever the instance. */
        const val REDIRECT_SCHEME = "dev.vidtheque.app"
        const val REDIRECT_PATH = "/oauth/callback"
        const val REDIRECT_URI = "$REDIRECT_SCHEME:$REDIRECT_PATH"
        const val SCOPE = "vidtheque:read vidtheque:write offline_access"

        fun clientId(base: String): String = "$base/auth/android/client.json"

        fun hostOf(base: String): String = base.substringAfter("://").substringBefore('/')

        /**
         * What the reader typed, as the instance's base URL: https, no query, no
         * trailing slash. Plain http only to this phone's own loopback in a debug
         * build, where a local stack answers through `adb reverse`.
         */
        fun normalize(typed: String, allowLoopbackHttp: Boolean = BuildConfig.DEBUG): String {
            val text = typed.trim().trimEnd('/')
            if (text.isEmpty()) throw InvalidInstance("Enter your instance's address.")
            val url = (if ("://" in text) text else "https://$text").toHttpUrlOrNull()
                ?: throw InvalidInstance("$text is not a web address.")
            val loopback = url.host == "localhost" || url.host == "127.0.0.1"
            if (url.scheme != "https" && !(allowLoopbackHttp && loopback)) throw InvalidInstance("The instance must be on https.")
            if (url.username.isNotEmpty() || url.password.isNotEmpty() || url.query != null || url.fragment != null) {
                throw InvalidInstance("Enter only the instance's address, like vidtheque.example.com.")
            }
            return url.toString().trimEnd('/')
        }
    }
}

class InvalidInstance(message: String) : Exception(message)
