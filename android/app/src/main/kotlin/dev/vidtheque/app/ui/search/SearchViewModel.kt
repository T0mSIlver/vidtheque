package dev.vidtheque.app.ui.search

import android.net.Uri
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dev.vidtheque.app.data.Api
import dev.vidtheque.app.data.ApiException
import dev.vidtheque.app.data.SearchHit
import java.io.IOException
import javax.inject.Inject
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.SerializationException

data class SearchUi(
    /** The query the hits answer; empty until one is submitted. */
    val query: String = "",
    val hits: List<SearchHit> = emptyList(),
    val notes: List<String> = emptyList(),
    /** Where the next page starts, or null when the server said there is no more. */
    val nextOffset: Int? = null,
    val loading: Boolean = false,
    /** Set on an empty first page: nothing matched, or nothing is indexed. */
    val empty: String? = null,
    val error: String? = null,
)

/** The MCP `search` tool's search over every channel, paged on `has_more` (dashboard.md §25.9). */
@HiltViewModel
class SearchViewModel @Inject constructor(private val api: Api) : ViewModel() {
    private val _ui = MutableStateFlow(SearchUi())
    val ui: StateFlow<SearchUi> = _ui.asStateFlow()
    private var paging: Job? = null

    /** A submitted query is one `mcp_search` signal; paging sends none. */
    fun submit(text: String) {
        val query = text.trim()
        if (query.isEmpty()) return
        paging?.cancel()
        _ui.value = SearchUi(query = query, loading = true)
        viewModelScope.launch { runCatching { api.searched(query) } }
        page(query, 0)
    }

    fun more() {
        val now = _ui.value
        val offset = now.nextOffset ?: return
        if (now.loading) return
        _ui.update { it.copy(loading = true, error = null) }
        page(now.query, offset)
    }

    fun retry() {
        val now = _ui.value
        if (now.query.isEmpty() || now.loading) return
        _ui.update { it.copy(loading = true, error = null) }
        page(now.query, now.nextOffset ?: 0)
    }

    /** Opening a moment is watching it, as on the video page. */
    fun watched(hit: SearchHit) {
        viewModelScope.launch { runCatching { api.signal("watch", hit.videoId, (hit.matchStart ?: hit.start).toInt()) } }
    }

    private fun page(query: String, offset: Int) {
        paging = viewModelScope.launch {
            try {
                val page = api.search(query, offset)
                val p = page.pagination
                _ui.update { now ->
                    val hits = now.hits + page.results
                    now.copy(
                        hits = hits,
                        notes = (now.notes + page.notes).distinct(),
                        nextOffset = if (p.hasMore) p.offset + p.limit else null,
                        loading = false,
                        empty = when {
                            hits.isNotEmpty() -> null
                            // Set on every empty page; only `empty` means nothing is indexed.
                            page.dataStatus == "empty" -> "Nothing is indexed yet."
                            else -> "Nothing matched. Try other words."
                        },
                    )
                }
            } catch (e: ApiException) {
                _ui.update { it.copy(loading = false, error = e.message) }
            } catch (e: IOException) {
                _ui.update { it.copy(loading = false, error = "The instance did not answer.") }
            } catch (e: SerializationException) {
                _ui.update { it.copy(loading = false, error = "The instance answered in a shape this app cannot read.") }
            }
        }
    }
}

/** A hit's link, admitted only as an HTTPS `youtu.be` link with a numeric `t` (dashboard.md §14). */
fun receipt(link: String): Uri? {
    val uri = runCatching { Uri.parse(link) }.getOrNull() ?: return null
    val id = uri.path?.trim('/').orEmpty()
    val t = runCatching { uri.getQueryParameter("t") }.getOrNull()
    if (uri.scheme != "https" || uri.host != "youtu.be" || id.isEmpty()) return null
    if (t.isNullOrEmpty() || !t.all { it.isDigit() }) return null
    return uri
}
