package dev.vidtheque.app.ui

import dev.vidtheque.app.auth.Instance
import dev.vidtheque.app.data.Api
import dev.vidtheque.app.ui.feed.FeedViewModel
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
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
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

// The filters are query parameters (dashboard.md §25.2): these pin which reads they
// send, that typing sends one read once it pauses, and that a "More" page keeps them.
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
class FeedViewModelTest {
    private val server = MockWebServer()
    private val main = StandardTestDispatcher()
    private val test = TestScope(main)

    @Before
    fun setUp() {
        Dispatchers.setMain(main)
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest) = when {
                request.url.encodedPath.endsWith("/feed/facets") -> MockResponse(
                    body = """{"channels":[{"name":"GPU MODE","count":1}],"entries":[{"entry_id":15,"text":"Evals","direction":"up","count":1}],"other":2}""",
                )
                else -> MockResponse(
                    body = """{"items":[{"video_id":"a","title":"t","score":2}],""" +
                        """"pagination":{"has_more":true,"next_offset":20},"skipped":{"count":0,"capped":false}}""",
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

    private fun settle(done: () -> Boolean) {
        val until = System.currentTimeMillis() + 5_000
        while (!done() && System.currentTimeMillis() < until) {
            test.runCurrent()
            Thread.sleep(10)
        }
        test.runCurrent()
    }

    /** The query strings of the feed reads so far, facets left out. */
    private fun reads(): List<String> = generateSequence { server.takeRequest(200, TimeUnit.MILLISECONDS) }
        .filter { it.url.encodedPath.endsWith("/feed") }
        .map { it.url.encodedQuery.orEmpty() }
        .toList()

    private fun model() = FeedViewModel(Api(OkHttpClient(), Instance(server.url("/").toString().trimEnd('/')))).also { m ->
        settle { m.ui.value.loaded && m.ui.value.facets != null }
    }

    @Test
    fun theFiltersAreSentAndKeptByTheNextPage() {
        val model = model()
        assertEquals(15L, model.ui.value.facets!!.entries.single().entryId)
        model.channel("GPU MODE")
        settle { !model.ui.value.refreshing }
        model.entry("other")
        settle { !model.ui.value.refreshing }
        model.oldest(true)
        settle { !model.ui.value.refreshing }
        model.more()
        settle { model.ui.value.top.items.size == 2 }
        val sent = reads()
        assertEquals("band=top&offset=0&limit=20", sent[0])
        assertEquals("band=top&offset=0&limit=20&channel=GPU%20MODE", sent[1])
        assertEquals("band=top&offset=0&limit=20&channel=GPU%20MODE&entry=other&order=oldest", sent[3])
        assertEquals("band=top&offset=20&limit=20&channel=GPU%20MODE&entry=other&order=oldest", sent[4])
    }

    @Test
    fun typingSendsOneReadOnceItPauses() {
        val model = model()
        reads()
        model.search("g")
        model.search("gp")
        model.search("gpu")
        test.advanceTimeBy(299)
        test.runCurrent()
        assertEquals(emptyList<String>(), reads())
        test.advanceTimeBy(2)
        settle { !model.ui.value.refreshing }
        assertEquals(listOf("band=top&offset=0&limit=20&q=gpu"), reads())
    }
}
