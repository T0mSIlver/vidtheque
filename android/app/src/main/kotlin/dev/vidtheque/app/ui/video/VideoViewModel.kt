package dev.vidtheque.app.ui.video

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.assisted.Assisted
import dagger.assisted.AssistedFactory
import dagger.assisted.AssistedInject
import dagger.hilt.android.lifecycle.HiltViewModel
import dev.vidtheque.app.data.Api
import dev.vidtheque.app.data.ApiException
import dev.vidtheque.app.data.Verdict
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.IOException

/** Waiting is the undo window: the signal is not sent yet, and a tap or Undo takes it back. */
enum class Sent { Waiting, Sending, Done, Failed }

/** How long a thumb or "less like this" stays undoable before it is sent. */
const val UNDO_MS = 5_000L

data class VideoUi(
    val verdict: Verdict? = null,
    val error: String? = null,
    val sent: Map<String, Sent> = emptyMap(),
    /** The kind tapped last, so the Undo bar follows the newest tap. */
    val latest: String? = null,
)

/** Every tap here is a signal the nightly update reads (companion.md §2.3). */
@HiltViewModel(assistedFactory = VideoViewModel.Factory::class)
class VideoViewModel @AssistedInject constructor(
    private val api: Api,
    @Assisted private val videoId: String,
) : ViewModel() {
    @AssistedFactory
    interface Factory {
        fun create(videoId: String): VideoViewModel
    }

    private val _ui = MutableStateFlow(VideoUi())
    val ui: StateFlow<VideoUi> = _ui.asStateFlow()
    private var opened = false

    init {
        load()
    }

    fun load() {
        _ui.update { it.copy(error = null) }
        viewModelScope.launch {
            try {
                _ui.update { it.copy(verdict = api.verdict(videoId)) }
                // `open` once per visit, when the verdict is on screen.
                if (!opened) {
                    opened = true
                    quietly("open")
                }
            } catch (e: ApiException) {
                _ui.update { it.copy(error = e.message) }
            } catch (e: IOException) {
                _ui.update { it.copy(error = "The instance did not answer.") }
            }
        }
    }

    private val waiting = mutableMapOf<String, Job>()

    /**
     * Thumbs and "less like this": the server cannot take a signal back (dashboard.md
     * §25.4), so each waits [UNDO_MS] here before it is sent, and a second tap in that
     * window undoes it. Leaving the screen sends it at once. Up and down exclude each
     * other while they wait. Once sent, a signal stays, one per kind per visit.
     */
    fun toggle(kind: String) {
        when (_ui.value.sent[kind]) {
            Sent.Waiting -> undo(kind)
            Sent.Sending, Sent.Done -> Unit
            null, Sent.Failed -> {
                OPPOSITE[kind]?.let { if (_ui.value.sent[it] == Sent.Waiting) undo(it) }
                mark(kind, Sent.Waiting)
                _ui.update { it.copy(latest = kind) }
                // Atomic: a coroutine cancelled before it starts skips its finally, and
                // that would lose a tap made just before leaving.
                waiting[kind] = viewModelScope.launch(start = CoroutineStart.ATOMIC) {
                    try {
                        delay(UNDO_MS)
                    } finally {
                        // Cancelled by undo: the state is no longer Waiting. Cancelled because
                        // the screen left: still Waiting, so send it now.
                        if (_ui.value.sent[kind] == Sent.Waiting) withContext(NonCancellable) { deliver(kind) }
                    }
                }
            }
        }
    }

    fun undo(kind: String) {
        if (_ui.value.sent[kind] != Sent.Waiting) return
        _ui.update { it.copy(sent = it.sent - kind) }
        waiting.remove(kind)?.cancel()
    }

    private suspend fun deliver(kind: String) {
        mark(kind, Sent.Sending)
        mark(kind, if (runCatching { api.signal(kind, videoId) }.isSuccess) Sent.Done else Sent.Failed)
    }

    fun watched(offsetS: Double) = quietly("watch", offsetS.toInt())

    fun askedClaude() = quietly("ask_claude")

    private fun quietly(kind: String, offsetS: Int? = null) {
        viewModelScope.launch { runCatching { api.signal(kind, videoId, offsetS) } }
    }

    private companion object {
        val OPPOSITE = mapOf("thumb_up" to "thumb_down", "thumb_down" to "thumb_up")
    }

    private fun mark(kind: String, state: Sent) = _ui.update { it.copy(sent = it.sent + (kind to state)) }
}
