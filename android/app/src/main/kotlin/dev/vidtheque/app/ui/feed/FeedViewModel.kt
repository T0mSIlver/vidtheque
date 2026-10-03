package dev.vidtheque.app.ui.feed

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dev.vidtheque.app.data.Api
import dev.vidtheque.app.data.ApiException
import dev.vidtheque.app.data.FeedItem
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.io.IOException
import javax.inject.Inject

/** One band of verdicts as the server pages it: `top` (2–3) or `skipped` (0–1). */
data class Band(val items: List<FeedItem> = emptyList(), val nextOffset: Int? = 0, val loading: Boolean = false)

data class FeedUi(
    val top: Band = Band(),
    val skipped: Band? = null,
    val skippedCount: Int = 0,
    val skippedCapped: Boolean = false,
    val loaded: Boolean = false,
    val refreshing: Boolean = false,
    val error: String? = null,
)

@HiltViewModel
class FeedViewModel @Inject constructor(private val api: Api) : ViewModel() {
    private val _ui = MutableStateFlow(FeedUi())
    val ui: StateFlow<FeedUi> = _ui.asStateFlow()
    private var paging: Job? = null

    init {
        refresh()
    }

    fun refresh() {
        paging?.cancel()
        _ui.update { it.copy(refreshing = true, error = null) }
        paging = viewModelScope.launch {
            load {
                val page = api.feed("top", 0)
                _ui.value = FeedUi(
                    top = Band(page.items, page.pagination.nextOffset.takeIf { page.pagination.hasMore }),
                    skippedCount = page.skipped.count,
                    skippedCapped = page.skipped.capped,
                    loaded = true,
                )
            }
            _ui.update { it.copy(refreshing = false) }
        }
    }

    /** Forget this session's feed (sign-out); the next sign-in loads it again. */
    fun clear() {
        paging?.cancel()
        _ui.value = FeedUi()
    }

    /** The next page of the top band, when the list nears its end. */
    fun more() = page(skipped = false)

    /** "Skipped (n)" opens the 0–1 band; tapping again folds it. */
    fun toggleSkipped() {
        if (_ui.value.skipped != null) _ui.update { it.copy(skipped = null) } else page(skipped = true)
    }

    fun moreSkipped() = page(skipped = true)

    private fun page(skipped: Boolean) {
        val band = (if (skipped) _ui.value.skipped ?: Band() else _ui.value.top)
        val offset = band.nextOffset ?: return
        if (band.loading || paging?.isActive == true) return
        set(skipped, band.copy(loading = true))
        paging = viewModelScope.launch {
            load {
                val page = api.feed(if (skipped) "skipped" else "top", offset)
                // Folded while the page was on its way: leave it folded.
                if (skipped && _ui.value.skipped == null) return@load
                set(skipped, Band(band.items + page.items, page.pagination.nextOffset.takeIf { page.pagination.hasMore }))
            }
            val now = if (skipped) _ui.value.skipped else _ui.value.top
            if (now?.loading == true) set(skipped, now.copy(loading = false))
        }
    }

    private fun set(skipped: Boolean, band: Band) =
        _ui.update { if (skipped) it.copy(skipped = band) else it.copy(top = band) }

    private suspend fun load(block: suspend () -> Unit) {
        try {
            block()
        } catch (e: ApiException) {
            _ui.update { it.copy(error = e.message) }
        } catch (e: IOException) {
            _ui.update { it.copy(error = "The instance did not answer. Pull to try again.") }
        }
    }
}
