package dev.vidtheque.app.data

import dev.vidtheque.app.auth.Instance
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runCurrent
import mockwebserver3.Dispatcher
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import mockwebserver3.RecordedRequest
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class MemoryHandOffs : HandOffStore {
    private var held: HandOff? = null
    override fun read() = held
    override fun write(handOff: HandOff?) {
        held = handOff
    }
}

// The time away from the app after a hand-off is the watch's length (dashboard.md §25.10).
@OptIn(ExperimentalCoroutinesApi::class)
class WatchClockTest {
    private val server = MockWebServer()
    private val test = TestScope(StandardTestDispatcher())
    private val store = MemoryHandOffs()
    private var now = 1_000_000L
    private lateinit var clock: WatchClock

    @Before
    fun setUp() {
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest) = MockResponse(
                body = if (request.url.encodedPath.endsWith("/signals")) """{"recorded":true,"signal_id":7}""" else "{}",
            )
        }
        server.start()
        clock = WatchClock(Api(OkHttpClient(), Instance(server.url("/").toString().trimEnd('/'))), store, { now }, test)
    }

    @After
    fun tearDown() = server.close()

    private fun settle(done: () -> Boolean) {
        val until = System.currentTimeMillis() + 5_000
        while (!done() && System.currentTimeMillis() < until) {
            test.runCurrent()
            Thread.sleep(10)
        }
    }

    private fun bodies(): List<String> = generateSequence { server.takeRequest(200, TimeUnit.MILLISECONDS) }.map { it.url.encodedPath + " " + it.body!!.utf8() }.toList()

    @Test
    fun theReturnSendsTheTimeAwayOnTheWatchItClosed() {
        clock.handOff("vid", 840)
        settle { store.read()?.signalId == 7L }
        now += 95_000
        clock.returned()
        settle { store.read() == null }
        val sent = bodies()
        assertEquals(2, sent.size)
        assertTrue(sent[0].contains("/signals") && sent[0].contains("\"kind\":\"watch\"") && sent[0].contains("\"offset_s\":840"))
        assertTrue(sent[1].contains("/watched") && sent[1].contains("\"signal_id\":7") && sent[1].contains("\"watched_s\":95.0"))
    }

    @Test
    fun aReturnBeforeTheWatchLandedClosesNothing() {
        clock.handOff("vid", 0)
        clock.returned()
        assertNull(store.read())
        settle { false }
        // The watch still lands, but its late answer does not reopen the hand-off.
        assertNull(store.read())
        assertTrue(bodies().none { it.contains("/watched") })
    }

    @Test
    fun aReturnTheNetworkLostIsSentBeforeTheNextHandOff() {
        store.write(HandOff(startedMs = 1L, signalId = 5, awayS = 42.0))
        clock.handOff("vid", 10)
        settle { store.read()?.signalId == 7L }
        val sent = bodies()
        assertTrue(sent.any { it.contains("/watched") && it.contains("\"signal_id\":5") && it.contains("\"watched_s\":42.0") })
    }

    @Test
    fun aPickFromOutsideSendsItsOwnWatchAndNoSignal() {
        clock.handOffOutside(4, 600)
        now += 120_000
        clock.returned()
        settle { store.read() == null }
        val sent = bodies()
        assertEquals(1, sent.size)
        assertTrue(sent[0].contains("/outside/watched") && sent[0].contains("\"id\":4") && sent[0].contains("\"offset_s\":600") && sent[0].contains("\"watched_s\":120.0"))
    }

    @Test
    fun aStartWithNoHandOffSendsNothing() {
        clock.returned()
        settle { false }
        assertTrue(bodies().isEmpty())
    }
}
