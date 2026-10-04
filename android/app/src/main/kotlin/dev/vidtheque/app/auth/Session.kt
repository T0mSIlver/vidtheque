package dev.vidtheque.app.auth

import android.net.Uri
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.Serializable
import javax.inject.Inject
import javax.inject.Singleton

enum class SessionState { Loading, SignedOut, SignedIn }

/** What a sign-in in flight must remember until the browser comes back, [instance] included. */
@Serializable
data class PendingSignIn(val verifier: String, val state: String, val url: String, val instance: String)

/**
 * The signed-in session: one access token, refreshed when it expires or a call
 * answers 401, and dropped when the server refuses the refresh token.
 */
@Singleton
class Session @Inject constructor(
    private val oauth: OAuthClient,
    private val instance: Instance,
    private val store: TokenStore,
    private val clock: Clock,
    scope: CoroutineScope,
) {
    private val _state = MutableStateFlow(SessionState.Loading)
    val state: StateFlow<SessionState> = _state.asStateFlow()

    private val lock = Mutex()
    private var tokens: Tokens? = null
    private var meta: Pair<String, ServerMetadata>? = null

    init {
        scope.launch {
            // An unreadable store (corrupt file, I/O error) is a signed-out session, not a hung splash.
            // A debug build pointed at a local token-mode stack starts signed in.
            tokens = (
                runCatching { store.load() }.getOrNull()
                    ?: instance.devToken.takeIf { it.isNotEmpty() }?.let { Tokens(it, null, Long.MAX_VALUE) }
                )
                // A session stored before the instance was a choice belongs to the build's
                // default; a build without one (the public release) signs it out.
                ?.let { if (it.instance.isNotEmpty()) it else instance.default.takeIf(String::isNotEmpty)?.let(it::withInstance) }
            tokens?.let { instance.base = it.instance }
            instance.restored()
            _state.value = if (tokens != null) SessionState.SignedIn else SessionState.SignedOut
        }
    }

    /** Check [base] is an instance this app can sign in to, then build its authorize URL. */
    suspend fun begin(base: String): PendingSignIn {
        val found = metadata(base)
        oauth.checkClient(base)
        val verifier = Pkce.secret()
        val state = Pkce.secret()
        return PendingSignIn(verifier, state, oauth.authorizeUrl(found, base, Pkce.challenge(verifier), state).toString(), base)
    }

    /** The redirect came back: check it belongs to [pending], then trade the code. */
    suspend fun complete(redirect: Uri, pending: PendingSignIn) {
        // State first: until it matches, nothing else on the URL is the server's to trust.
        if (redirect.getQueryParameter("state") != pending.state) {
            throw OAuthException("state_mismatch", "This sign-in answer belongs to another attempt. Sign in again.")
        }
        redirect.getQueryParameter("error")?.let {
            throw OAuthException(it, redirect.getQueryParameter("error_description") ?: "The server refused the sign-in ($it).")
        }
        val meta = metadata(pending.instance)
        // RFC 9207: the server names itself on the redirect; a different name is a mix-up.
        redirect.getQueryParameter("iss")?.let {
            if (it.trimEnd('/') != meta.issuer.trimEnd('/')) throw OAuthException("invalid_issuer", "The sign-in answer came from $it.")
        }
        val code = redirect.getQueryParameter("code") ?: throw OAuthException("no_code", "The sign-in answer carried no code.")
        val issued = oauth.exchange(meta, pending.instance, code, pending.verifier)
        lock.withLock {
            instance.base = pending.instance
            keep(issued, previousRefresh = null)
        }
        _state.value = SessionState.SignedIn
    }

    /** A valid access token for a request to [url], refreshed first when it is about to expire;
     *  null when signed out or when [url] is not on the instance that issued it. */
    suspend fun accessToken(url: String): String? = lock.withLock {
        val current = tokens?.takeIf { it.belongTo(url) } ?: return null
        if (clock.nowMs() < current.expiresAtMs - EARLY_MS) current.access else refreshLocked(current)
    }

    /** A call to [url] was refused with [rejected]; answer the token to retry with, or null to give up.
     *  A call that left before a switch of instance gets nothing: the new token is not its server's. */
    suspend fun afterUnauthorized(rejected: String, url: String): String? = lock.withLock {
        val current = tokens?.takeIf { it.belongTo(url) } ?: return null
        // Another call already refreshed while this one was in flight.
        if (current.access != rejected) current.access else refreshLocked(current)
    }

    suspend fun signOut() {
        val dropped = lock.withLock { tokens.also { tokens = null } }
        store.clear()
        _state.value = SessionState.SignedOut
        dropped?.refresh?.let { runCatching { oauth.revoke(metadata(dropped.instance), dropped.instance, it) } }
    }

    private suspend fun refreshLocked(current: Tokens): String? {
        val refresh = current.refresh ?: return endLocked()
        return try {
            keep(oauth.refresh(metadata(current.instance), current.instance, refresh), previousRefresh = refresh).access
        } catch (e: OAuthException) {
            // invalid_grant: the refresh token is spent or revoked; anything else may pass.
            if (e.error == "invalid_grant") endLocked() else throw e
        }
    }

    private suspend fun endLocked(): String? {
        tokens = null
        store.clear()
        _state.value = SessionState.SignedOut
        return null
    }

    private suspend fun keep(response: TokenResponse, previousRefresh: String?): Tokens {
        // The server rotates refresh tokens; keep the old one only if it sent none.
        val next = Tokens(response.accessToken, response.refreshToken ?: previousRefresh, clock.nowMs() + response.expiresIn * 1000, instance.base)
        tokens = next
        store.save(next)
        return next
    }

    private suspend fun metadata(base: String): ServerMetadata =
        meta?.takeIf { it.first == base }?.second ?: oauth.metadata(base).also { meta = base to it }

    private companion object {
        const val EARLY_MS = 60_000L
    }
}

fun interface Clock {
    fun nowMs(): Long
}
