package dev.vidtheque.app.ui

import dev.vidtheque.app.auth.Instance
import dev.vidtheque.app.data.Api
import dev.vidtheque.app.ui.search.SearchViewModel
import dev.vidtheque.app.ui.search.evidence
import dev.vidtheque.app.ui.search.receipt
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
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

// The search screen (dashboard.md §25.9): one `mcp_search` per submitted query and
// none for paging, pages on has_more, and a receipt only for a youtu.be link with a t.
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
class SearchViewModelTest {
    private val server = MockWebServer()
    private val main = StandardTestDispatcher()
    private val test = TestScope(main)

    private fun hit(id: String, at: Int) =
        """{"video_id":"$id","title":"t $id","channel":"c","source":"transcript","start":$at,"match_start":$at,"text":"said","link":"https://youtu.be/$id?t=${at - 2}","published_at":1674000000}"""

    @Before
    fun setUp() {
        Dispatchers.setMain(main)
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.url.encodedPath
                return when {
                    request.method == "POST" -> MockResponse(body = "{}")
                    path.endsWith("/verdicts/unjudged") -> MockResponse(
                        code = 404,
                        body = """{"error":"E_NO_VERDICT","message":"no verdict","next":"later","video":{"video_id":"unjudged","title":"Plain","channel":null,"duration_s":60,"published_at":null}}""",
                    )
                    request.url.queryParameter("offset") == "0" -> MockResponse(
                        body = """{"results":[${hit("a", 10)},${hit("b", 20)}],"pagination":{"limit":2,"offset":0,"has_more":true},"notes":["a note"]}""",
                    )
                    else -> MockResponse(body = """{"results":[${hit("c", 30)}],"pagination":{"limit":2,"offset":2,"has_more":false},"notes":["a note"]}""")
                }
            }
        }
        server.start()
    }

    @After
    fun tearDown() {
        server.close()
        Dispatchers.resetMain()
    }

    private fun settle(done: () -> Boolean) {
        val until = System.currentTimeMillis() + 5_000
        while (!done() && System.currentTimeMillis() < until) {
            test.runCurrent()
            Thread.sleep(10)
        }
        test.runCurrent()
    }

    private fun api() = Api(OkHttpClient(), Instance(server.url("/").toString().trimEnd('/')))

    private fun requests() = generateSequence { server.takeRequest(200, TimeUnit.MILLISECONDS) }.toList()

    @Test
    fun aQueryIsOneSignalAndPagingSendsNone() {
        val model = SearchViewModel(api())
        model.submit("  kv cache ")
        settle { model.ui.value.hits.size == 2 }
        assertEquals(2, model.ui.value.nextOffset)
        model.more()
        settle { model.ui.value.hits.size == 3 }
        assertNull(model.ui.value.nextOffset)
        // The same note on both pages prints once.
        assertEquals(listOf("a note"), model.ui.value.notes)
        val sent = requests()
        val signals = sent.filter { it.method == "POST" }.map { it.body!!.utf8() }
        assertEquals(1, signals.size)
        assertTrue(signals[0].contains("\"kind\":\"mcp_search\"") && signals[0].contains("\"text\":\"kv cache\""))
        val searches = sent.filter { it.method == "GET" }.map { it.url.encodedQuery }
        assertEquals(listOf("q=kv+cache&offset=0", "q=kv+cache&offset=2"), searches)
    }

    @Test
    fun aMomentIsAWatchAtTheSecondThatMatched() {
        val model = SearchViewModel(api())
        model.submit("x")
        settle { model.ui.value.hits.isNotEmpty() }
        requests()
        model.watched(model.ui.value.hits[1])
        settle { false }
        val body = requests().single { it.method == "POST" }.body!!.utf8()
        assertTrue(body.contains("\"kind\":\"watch\"") && body.contains("\"video_id\":\"b\"") && body.contains("\"offset_s\":20"))
    }

    @Test
    fun anUnjudgedVideoIsDrawnFromTheRefusal() {
        val model = VideoViewModel(api(), "unjudged")
        settle { model.ui.value.unjudged != null || model.ui.value.error != null }
        assertEquals("Plain", model.ui.value.unjudged?.title)
        assertNull(model.ui.value.error)
    }

    @Test
    fun onlyAYoutuBeLinkWithASecondIsAReceipt() {
        assertEquals("https://youtu.be/-AbC?t=12", receipt("https://youtu.be/-AbC?t=12").toString())
        for (bad in listOf("http://youtu.be/a?t=1", "https://youtube.com/a?t=1", "https://youtu.be/a", "https://youtu.be/a?t=1s", "https://youtu.be/?t=1", "")) {
            assertNull(bad, receipt(bad))
        }
        assertEquals("spoken · on-screen", evidence("ocr+transcript"))
        assertEquals("", evidence("mystery"))
    }
}
