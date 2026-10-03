package dev.vidtheque.app.auth

import android.net.Uri
import dev.vidtheque.app.net.authorized
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import mockwebserver3.Dispatcher
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import mockwebserver3.RecordedRequest
import okhttp3.OkHttpClient
import okhttp3.Request
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

private class MemoryStore : TokenStore {
    var tokens: Tokens? = null
    override suspend fun load() = tokens
    override suspend fun save(tokens: Tokens) { this.tokens = tokens }
    override suspend fun clear() { tokens = null }
}

// A fake instance with the server's real refusals: it rotates refresh tokens, and
// /dashboard/api/feed answers 401 to any access token but the latest.
private class FakeInstance : Dispatcher() {
    var issued = 0
    var latestAccess = ""
    var liveRefresh = ""
    val forms = mutableListOf<String>()
    lateinit var base: String

    override fun dispatch(request: RecordedRequest): MockResponse {
        val path = request.url.encodedPath
        return when (path) {
            "/.well-known/oauth-authorization-server" -> json(
                """{"issuer":"$base","authorization_endpoint":"$base/authorize","token_endpoint":"$base/token"}""",
            )
            "/token" -> {
                val form = request.body!!.utf8()
                forms += form
                if ("grant_type=refresh_token" in form && "refresh_token=$liveRefresh" !in form) {
                    MockResponse(code = 400, body = """{"error":"invalid_grant"}""")
                } else {
                    issued++
                    latestAccess = "access-$issued"
                    liveRefresh = "refresh-$issued"
                    json("""{"access_token":"$latestAccess","refresh_token":"$liveRefresh","expires_in":3600}""")
                }
            }
            else -> if (request.headers["Authorization"] == "Bearer $latestAccess") json("{}") else MockResponse(code = 401)
        }
    }

    private fun json(body: String) = MockResponse(code = 200, body = body)
}

@RunWith(RobolectricTestRunner::class)
class SessionTest {
    private val server = MockWebServer()
    private val fake = FakeInstance()
    private val store = MemoryStore()
    private var now = 0L

    @Before fun start() {
        server.dispatcher = fake
        server.start()
        fake.base = server.url("/").toString().trimEnd('/')
    }

    @After fun stop() = server.close()

    private fun TestScope.session(): Session {
        val instance = Instance(fake.base)
        return Session(OAuthClient(OkHttpClient(), instance), store, { now }, CoroutineScope(StandardTestDispatcher(testScheduler)))
            .also { advanceUntilIdle() }
    }

    private fun redirect(pending: PendingSignIn, extra: String = "") =
        Uri.parse("${fake.base}/auth/android/callback?code=c1&state=${pending.state}$extra")

    @Test fun signInTradesTheCodeWithTheVerifierItChallengedWith() = runTest {
        val session = session()
        val pending = session.begin()
        val url = Uri.parse(pending.url)
        assertEquals(Pkce.challenge(pending.verifier), url.getQueryParameter("code_challenge"))
        assertEquals("${fake.base}/auth/android/client.json", url.getQueryParameter("client_id"))

        session.complete(redirect(pending), pending)

        assertEquals(SessionState.SignedIn, session.state.value)
        assert("code_verifier=${pending.verifier}" in fake.forms.single())
        assertEquals("access-1", store.tokens?.access)
    }

    @Test fun anAnswerFromAnotherAttemptOrIssuerIsRefused() = runTest {
        val session = session()
        val pending = session.begin()
        val stale = Uri.parse("${fake.base}/auth/android/callback?code=c1&state=other")
        assertEquals("state_mismatch", assertThrows(OAuthException::class.java) { kotlinx.coroutines.runBlocking { session.complete(stale, pending) } }.error)
        val spoof = Uri.parse("${fake.base}/auth/android/callback?state=other&error=x&error_description=Call+this+number")
        assertEquals("state_mismatch", assertThrows(OAuthException::class.java) { kotlinx.coroutines.runBlocking { session.complete(spoof, pending) } }.error)
        assertEquals("invalid_issuer", assertThrows(OAuthException::class.java) { kotlinx.coroutines.runBlocking { session.complete(redirect(pending, "&iss=https%3A%2F%2Fevil.example"), pending) } }.error)
        assert(fake.forms.isEmpty())
    }

    @Test fun aRefusedCallRefreshesOnceAndRetries() = runTest {
        val session = session()
        val pending = session.begin()
        session.complete(redirect(pending), pending)
        fake.latestAccess = "rotated-server-side"  // the server no longer takes access-1

        val api = authorized(OkHttpClient(), session)
        val code = api.newCall(Request.Builder().url("${fake.base}/dashboard/api/feed").build()).execute().use { it.code }

        assertEquals(200, code)
        assertEquals("access-2", store.tokens?.access)
        assertEquals("refresh-2", store.tokens?.refresh)
    }

    @Test fun anExpiredTokenIsRefreshedBeforeTheCall() = runTest {
        val session = session()
        val pending = session.begin()
        session.complete(redirect(pending), pending)
        now += 3_600_000

        assertEquals("access-2", session.accessToken())
    }

    @Test fun aRevokedRefreshTokenSignsOut() = runTest {
        val session = session()
        val pending = session.begin()
        session.complete(redirect(pending), pending)
        fake.liveRefresh = "revoked"
        now += 3_600_000

        assertNull(session.accessToken())
        assertEquals(SessionState.SignedOut, session.state.value)
        assertNull(store.tokens)
    }
}
