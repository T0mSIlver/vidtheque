package dev.vidtheque.app.ui

import androidx.lifecycle.viewModelScope
import dev.vidtheque.app.auth.Instance
import dev.vidtheque.app.data.Api
import dev.vidtheque.app.ui.video.Sent
import dev.vidtheque.app.ui.video.UNDO_MS
import dev.vidtheque.app.ui.video.VideoViewModel
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.cancel
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
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
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

// The server cannot take a signal back, so the undo window is the app's alone: these
// pin that an undone tap never reaches the server and a pending one survives leaving.
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
class VideoViewModelTest {
    private val server = MockWebServer()
    private val main = StandardTestDispatcher()
    private val test = TestScope(main)

    @Before
    fun setUp() {
        Dispatchers.setMain(main)
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest) =
                if (request.method == "POST") MockResponse(body = """{"recorded":true}""") else MockResponse(code = 404, body = """{"error":"E_NO_VERDICT","message":"Not judged yet."}""")
        }
        server.start()
    }

    @After
    fun tearDown() {
        server.close()
        Dispatchers.resetMain()
    }

    private fun model() = VideoViewModel(Api(OkHttpClient(), Instance(server.url("/").toString().trimEnd('/'))), "vid").also {
        test.runCurrent()
        server.takeRequest(5, TimeUnit.SECONDS) // the verdict GET
    }

    private fun signal(): String? = server.takeRequest(2, TimeUnit.SECONDS)?.body?.utf8()

    @Test
    fun aTapUndoneInTheWindowSendsNothing() {
        val model = model()
        model.toggle("mute")
        test.advanceTimeBy(UNDO_MS / 2)
        model.toggle("mute")
        test.advanceTimeBy(UNDO_MS * 2)
        test.runCurrent()
        assertNull(signal())
        assertNull(model.ui.value.sent["mute"])
    }

    @Test
    fun aTapIsSentOnceTheWindowCloses() {
        val model = model()
        model.toggle("thumb_up")
        assertEquals(Sent.Waiting, model.ui.value.sent["thumb_up"])
        test.advanceTimeBy(UNDO_MS + 1)
        test.runCurrent()
        assertEquals(true, signal()?.contains("\"thumb_up\""))
    }

    @Test
    fun leavingTheScreenSendsAWaitingTap() {
        val model = model()
        model.toggle("thumb_down")
        model.viewModelScope.cancel()
        test.runCurrent()
        assertEquals(true, signal()?.contains("\"thumb_down\""))
    }
}
