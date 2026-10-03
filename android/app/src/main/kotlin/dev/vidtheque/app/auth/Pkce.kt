package dev.vidtheque.app.auth

import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64

/** RFC 7636 S256: a 256-bit verifier, its SHA-256 as the challenge. */
internal object Pkce {
    private val random = SecureRandom()
    private val b64 = Base64.getUrlEncoder().withoutPadding()

    fun secret(): String = b64.encodeToString(ByteArray(32).also(random::nextBytes))

    fun challenge(verifier: String): String =
        b64.encodeToString(MessageDigest.getInstance("SHA-256").digest(verifier.toByteArray(Charsets.US_ASCII)))
}
