package dev.vidtheque.app.ui

import dev.vidtheque.app.auth.Instance
import dev.vidtheque.app.data.Api
import dev.vidtheque.app.ui.video.VideoViewModel
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.setMain
import mockwebserver3.Dispatcher
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import mockwebserver3.RecordedRequest
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

// A thumb or mute is a state the server stores (companion.md §2.3): these pin that
// the screen starts from it, a second tap sends `none`, and a refusal puts it back.
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
class VideoViewModelTest {
    private val server = MockWebServer()
    private val main = StandardTestDispatcher()
    private val test = TestScope(main)
    private var refuse = false

    @Before
    fun setUp() {
        Dispatchers.setMain(main)
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest) = when {
                request.url.encodedPath.endsWith("/feedback") && refuse -> MockResponse(code = 500)
                request.method == "POST" -> MockResponse(body = "{}")
                else -> MockResponse(
                    body = """{"video":{"video_id":"vid","title":"t"},"score":2,"feedback":"muted"}""",
                )
            }
        }
        server.start()
    }

    @After
    fun tearDown() {
        server.close()
        Dispatchers.resetMain()
    }

    /** Runs the main dispatcher until [done] holds; the HTTP calls finish on OkHttp's threads. */
    private fun settle(done: () -> Boolean) {
        val until = System.currentTimeMillis() + 5_000
        while (!done() && System.currentTimeMillis() < until) {
            test.runCurrent()
            Thread.sleep(10)
        }
        test.runCurrent()
    }

    private fun model() = VideoViewModel(Api(OkHttpClient(), Instance(server.url("/").toString().trimEnd('/'))), "vid").also { m ->
        settle { m.ui.value.verdict != null }
    }

    /** The bodies POSTed to /feedback, in order, skipping the screen's `open` signal. */
    private fun sent(): List<String> = generateSequence { server.takeRequest(200, TimeUnit.MILLISECONDS) }
        .filter { it.url.encodedPath.endsWith("/feedback") }
        .map { it.body!!.utf8() }
        .toList()

    @Test
    fun theScreenShowsTheStoredStateAndASecondTapTakesItBack() {
        val model = model()
        assertEquals("muted", model.ui.value.feedback)
        model.tap("muted")
        settle { !model.ui.value.saving }
        assertEquals("none", model.ui.value.feedback)
        model.tap("up")
        settle { !model.ui.value.saving }
        assertEquals("up", model.ui.value.feedback)
        val bodies = sent()
        assertEquals(2, bodies.size)
        assertTrue(bodies[0].contains("\"state\":\"none\""))
        assertTrue(bodies[1].contains("\"state\":\"up\""))
    }

    @Test
    fun openIsSentOnceAndOnlyWhenThePageSettles() {
        // A neighbour page in the pager loads its verdict without having been opened.
        val model = model()
        model.shown()
        model.shown()
        settle { false }
        val opens = generateSequence { server.takeRequest(200, TimeUnit.MILLISECONDS) }
            .filter { it.url.encodedPath.endsWith("/signals") }
            .map { it.body!!.utf8() }
            .toList()
        assertEquals(1, opens.size)
        assertTrue(opens[0].contains("\"open\""))
    }

    @Test
    fun aRefusedTapPutsTheStoredStateBack() {
        val model = model()
        refuse = true
        model.tap("down")
        settle { !model.ui.value.saving }
        assertEquals("muted", model.ui.value.feedback)
        assertTrue(model.ui.value.failed)
    }
}
