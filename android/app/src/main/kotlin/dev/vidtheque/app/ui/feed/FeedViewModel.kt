package dev.vidtheque.app.ui.feed

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dev.vidtheque.app.data.Api
import dev.vidtheque.app.data.ApiException
import dev.vidtheque.app.data.FeedFacets
import dev.vidtheque.app.data.FeedFilters
import dev.vidtheque.app.data.FeedItem
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.io.IOException
import javax.inject.Inject

/** Every judged video as the server pages it (`band=all`), the list behind "Show all". */
data class Band(val items: List<FeedItem> = emptyList(), val nextOffset: Int? = 0, val loading: Boolean = false)

data class FeedUi(
    val top: Band = Band(),
    val loaded: Boolean = false,
    val refreshing: Boolean = false,
    val error: String? = null,
    val filters: FeedFilters = FeedFilters(),
    /** Null until read; a failed read leaves the filters without channels and entries. */
    val facets: FeedFacets? = null,
)

private const val BAND = "all"

/** How long typing pauses before the search is sent. */
private const val TYPING_MS = 300L

@HiltViewModel
class FeedViewModel @Inject constructor(private val api: Api) : ViewModel() {
    private val _ui = MutableStateFlow(FeedUi())
    val ui: StateFlow<FeedUi> = _ui.asStateFlow()
    private var paging: Job? = null
    private var typing: Job? = null
    private var faceting: Job? = null

    init {
        refresh()
    }

    fun refresh() {
        paging?.cancel()
        // A pending search would only read the same page again.
        typing?.cancel()
        _ui.update { it.copy(refreshing = true, error = null) }
        val filters = _ui.value.filters
        paging = viewModelScope.launch {
            load {
                val page = api.feed(BAND, 0, filters)
                _ui.update {
                    it.copy(top = Band(page.items, page.pagination.nextOffset.takeIf { page.pagination.hasMore }), loaded = true)
                }
            }
            _ui.update { it.copy(refreshing = false) }
        }
        // What the filters offer does not depend on them, so it is read beside the page.
        faceting?.cancel()
        faceting = viewModelScope.launch {
            try {
                val facets = api.facets()
                _ui.update { it.copy(facets = facets) }
            } catch (_: ApiException) {
            } catch (_: IOException) {
            }
        }
    }

    /** The search box: sent once typing pauses. */
    fun search(q: String) {
        if (q == _ui.value.filters.q) return
        _ui.update { it.copy(filters = it.filters.copy(q = q)) }
        typing?.cancel()
        typing = viewModelScope.launch {
            delay(TYPING_MS)
            refresh()
        }
    }

    fun channel(name: String?) = narrow { it.copy(channel = name) }

    /** A profile entry id, `other`, or null for all. */
    fun entry(id: String?) = narrow { it.copy(entry = id) }

    fun oldest(on: Boolean) = narrow { it.copy(oldest = on) }

    private fun narrow(change: (FeedFilters) -> FeedFilters) {
        val next = change(_ui.value.filters)
        if (next == _ui.value.filters) return
        typing?.cancel()
        _ui.update { it.copy(filters = next) }
        refresh()
    }

    /** Forget this session's feed (sign-out); the next sign-in loads it again. */
    fun clear() {
        paging?.cancel()
        typing?.cancel()
        faceting?.cancel()
        _ui.value = FeedUi()
    }

    /** The next page, when the list nears its end. */
    fun more() {
        val band = _ui.value.top
        val offset = band.nextOffset ?: return
        if (band.loading || paging?.isActive == true) return
        _ui.update { it.copy(top = band.copy(loading = true)) }
        paging = viewModelScope.launch {
            load {
                val page = api.feed(BAND, offset, _ui.value.filters)
                _ui.update { it.copy(top = Band(band.items + page.items, page.pagination.nextOffset.takeIf { page.pagination.hasMore })) }
            }
            if (_ui.value.top.loading) _ui.update { it.copy(top = it.top.copy(loading = false)) }
        }
    }

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
