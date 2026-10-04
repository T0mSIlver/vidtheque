package dev.vidtheque.app.ui.brief

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dev.vidtheque.app.data.Api
import dev.vidtheque.app.data.ApiException
import dev.vidtheque.app.data.Brief
import dev.vidtheque.app.data.Proposal
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.io.IOException
import javax.inject.Inject

data class BriefUi(
    val brief: Brief? = null,
    val loaded: Boolean = false,
    val busy: Boolean = false,
    val error: String? = null,
    /** Shown once the check-in reached the server. */
    val saved: Boolean = false,
)

/** The weekly brief (companion.md §6.1): read it, answer its check-in, revert, pause. */
@HiltViewModel
class BriefViewModel @Inject constructor(private val api: Api) : ViewModel() {
    private val _ui = MutableStateFlow(BriefUi())
    val ui: StateFlow<BriefUi> = _ui.asStateFlow()
    private var week: String? = null

    init {
        load()
    }

    fun load(week: String? = this.week) {
        this.week = week
        exclusive { _ui.update { it.copy(brief = api.brief(week), loaded = true, saved = false) } }
    }

    fun checkin(rating: Int, missing: String) {
        val brief = _ui.value.brief ?: return
        exclusive {
            api.checkin(brief.week, rating, missing)
            _ui.update { it.copy(saved = true) }
        }
    }

    fun revert(eventId: Long) = exclusive {
        api.revert(eventId)
        _ui.update { it.copy(brief = api.brief(week)) }
    }

    /** Only ever on the reader's tap: the brief suggests a pause, it never pauses. */
    fun pause(slug: String) = exclusive {
        api.pauseFollow(slug)
        _ui.update { it.copy(brief = api.brief(week)) }
    }

    private fun exclusive(block: suspend () -> Unit) {
        if (_ui.value.busy) return
        _ui.update { it.copy(error = null, busy = true) }
        viewModelScope.launch {
            _ui.update { it.copy(error = failure(block)) }
            _ui.update { it.copy(busy = false, loaded = true) }
        }
    }
}

/** What one skipped video's answer came to. */
data class SkipState(val answer: String? = null, val proposal: Proposal? = null, val eased: Boolean = false, val error: String? = null)

/**
 * "Why skipped" with a fix: the reader's word on skipped videos, from the feed's
 * rows and the brief's audit. "I'd watch this" sets the thumb up on the server
 * and answers a proposed reweight, applied only on [ease].
 */
@HiltViewModel
class SkipViewModel @Inject constructor(private val api: Api) : ViewModel() {
    private val _skips = MutableStateFlow<Map<String, SkipState>>(emptyMap())
    val skips: StateFlow<Map<String, SkipState>> = _skips.asStateFlow()

    fun answer(videoId: String, answer: String, source: String) = viewModelScope.launch {
        var proposal: Proposal? = null
        val error = failure { proposal = api.skip(videoId, answer, source).proposal }
        set(videoId) { if (error == null) SkipState(answer, proposal) else it.copy(error = error) }
    }

    fun ease(videoId: String, proposal: Proposal) = viewModelScope.launch {
        val error = failure { api.reweight(proposal.entryId, proposal.to, "eased after a skip you would have watched") }
        set(videoId) { it.copy(eased = error == null, error = error) }
    }

    private fun set(videoId: String, change: (SkipState) -> SkipState) =
        _skips.update { it + (videoId to change(it[videoId] ?: SkipState())) }
}

private suspend fun failure(block: suspend () -> Unit): String? = try {
    block()
    null
} catch (e: ApiException) {
    e.message
} catch (_: IOException) {
    "The instance did not answer."
}
