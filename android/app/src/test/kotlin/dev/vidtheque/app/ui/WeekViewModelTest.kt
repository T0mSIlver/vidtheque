package dev.vidtheque.app.ui

import dev.vidtheque.app.auth.Instance
import dev.vidtheque.app.data.Api
import dev.vidtheque.app.ui.week.WeekViewModel
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
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

// The week is one read (dashboard.md §25.13); the budget is set per day and sent per week.
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
class WeekViewModelTest {
    private val server = MockWebServer()
    private val main = StandardTestDispatcher()
    private val test = TestScope(main)
    private val sent = java.util.concurrent.CopyOnWriteArrayList<String>()

    @Before
    fun setUp() {
        Dispatchers.setMain(main)
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest) = when {
                sent.add("${request.method} ${request.url.encodedPath.substringAfterLast('/')}?${request.url.encodedQuery.orEmpty()} ${request.body?.utf8().orEmpty()}").let { false } -> MockResponse()
                request.url.encodedPath.endsWith("/budget") -> MockResponse(body = """{"week_budget_min":315}""")
                else -> MockResponse(
                    body = """{"week":"2026-09-28","previous":"2026-09-21","next":null,"budget_min":210,"asks_s":300.0,""" +
                        """"items":[{"video_id":"a","title":"t","score":2,"tier":2,"asks_s":300.0}],""" +
                        """"days":[],"rest":{"count":1,"asks_s":600.0},"capped":false}""",
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

    @Test
    fun anotherWeekIsReadByItsMondayAndTheBudgetIsSentAsAWeek() {
        val model = WeekViewModel(Api(OkHttpClient(), Instance(server.url("/").toString().trimEnd('/'))))
        model.refresh()
        settle { model.ui.value.week != null }
        assertEquals(1, model.ui.value.week!!.rest.count)
        model.go("2026-09-21")
        settle { !model.ui.value.refreshing }
        model.budget(45)
        settle { sent.size == 4 && !model.ui.value.refreshing }
        assertEquals(
            listOf("GET week? ", "GET week?week=2026-09-21 ", """POST budget? {"week_budget_min":315}""", "GET week?week=2026-09-21 "),
            sent.toList(),
        )
    }
}
