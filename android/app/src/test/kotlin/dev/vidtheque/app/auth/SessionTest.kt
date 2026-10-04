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
    var clientRedirects = """["dev.vidtheque.app:/oauth/callback"]"""

    override fun dispatch(request: RecordedRequest): MockResponse {
        val path = request.url.encodedPath
        return when (path) {
            "/.well-known/oauth-authorization-server" -> json(
                """{"issuer":"$base","authorization_endpoint":"$base/authorize","token_endpoint":"$base/token"}""",
            )
            "/auth/android/client.json" -> json("""{"redirect_uris":$clientRedirects}""")
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

    private var instance = Instance("")

    private fun TestScope.session(): Session {
        return Session(OAuthClient(OkHttpClient()), instance, store, { now }, CoroutineScope(StandardTestDispatcher(testScheduler)))
            .also { advanceUntilIdle() }
    }

    private fun redirect(pending: PendingSignIn, extra: String = "") =
        Uri.parse("dev.vidtheque.app:/oauth/callback?code=c1&state=${pending.state}$extra")

    @Test fun signInTradesTheCodeWithTheVerifierItChallengedWith() = runTest {
        val session = session()
        val pending = session.begin(fake.base)
        val url = Uri.parse(pending.url)
        assertEquals(Pkce.challenge(pending.verifier), url.getQueryParameter("code_challenge"))
        assertEquals("${fake.base}/auth/android/client.json", url.getQueryParameter("client_id"))
        assertEquals("dev.vidtheque.app:/oauth/callback", url.getQueryParameter("redirect_uri"))

        session.complete(redirect(pending), pending)

        assertEquals(SessionState.SignedIn, session.state.value)
        assert("code_verifier=${pending.verifier}" in fake.forms.single())
        assertEquals("access-1", store.tokens?.access)
        assertEquals(fake.base, store.tokens?.instance)
        assertEquals(fake.base, instance.base)
    }

    @Test fun anInstanceWithoutTheAppSchemeIsNamedTooOld() = runTest {
        fake.clientRedirects = """["${fake.base}/auth/android/callback"]"""
        val refused = assertThrows(OAuthException::class.java) { kotlinx.coroutines.runBlocking { session().begin(fake.base) } }
        assertEquals("old_instance", refused.error)
    }

    @Test fun aSessionFromBeforeTheChoiceKeepsTheDefaultOrSignsOut() = runTest {
        store.tokens = Tokens("a", "r", Long.MAX_VALUE)
        instance = Instance(fake.base)
        assertEquals(SessionState.SignedIn, session().state.value)
        assertEquals(fake.base, instance.base)

        instance = Instance("")
        assertEquals(SessionState.SignedOut, session().state.value)
    }

    @Test fun anAnswerFromAnotherAttemptOrIssuerIsRefused() = runTest {
        val session = session()
        val pending = session.begin(fake.base)
        val stale = Uri.parse("dev.vidtheque.app:/oauth/callback?code=c1&state=other")
        assertEquals("state_mismatch", assertThrows(OAuthException::class.java) { kotlinx.coroutines.runBlocking { session.complete(stale, pending) } }.error)
        val spoof = Uri.parse("dev.vidtheque.app:/oauth/callback?state=other&error=x&error_description=Call+this+number")
        assertEquals("state_mismatch", assertThrows(OAuthException::class.java) { kotlinx.coroutines.runBlocking { session.complete(spoof, pending) } }.error)
        assertEquals("invalid_issuer", assertThrows(OAuthException::class.java) { kotlinx.coroutines.runBlocking { session.complete(redirect(pending, "&iss=https%3A%2F%2Fevil.example"), pending) } }.error)
        assert(fake.forms.isEmpty())
    }

    @Test fun aRefusedCallRefreshesOnceAndRetries() = runTest {
        val session = session()
        val pending = session.begin(fake.base)
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
        val pending = session.begin(fake.base)
        session.complete(redirect(pending), pending)
        now += 3_600_000

        assertEquals("access-2", session.accessToken())
    }

    @Test fun aRevokedRefreshTokenSignsOut() = runTest {
        val session = session()
        val pending = session.begin(fake.base)
        session.complete(redirect(pending), pending)
        fake.liveRefresh = "revoked"
        now += 3_600_000

        assertNull(session.accessToken())
        assertEquals(SessionState.SignedOut, session.state.value)
        assertNull(store.tokens)
    }
}

class InstanceTest {
    @Test fun anAddressIsTheInstanceBaseOrARefusal() {
        assertEquals("https://vt.example.com", Instance.normalize(" vt.example.com/ "))
        assertEquals("https://vt.example.com:8443/vt", Instance.normalize("https://vt.example.com:8443/vt"))
        assertEquals("http://localhost:8790", Instance.normalize("http://localhost:8790", allowLoopbackHttp = true))
        for (bad in listOf("", "http://vt.example.com", "http://localhost:8790", "https://u:p@vt.example.com", "vt.example.com/?x=1")) {
            assertThrows(bad, InvalidInstance::class.java) { Instance.normalize(bad, allowLoopbackHttp = false) }
        }
    }
}
