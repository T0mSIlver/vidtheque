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
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.IOException

data class VideoUi(
    val verdict: Verdict? = null,
    val error: String? = null,
    /** The video's thumb or mute as stored: `up`, `down`, `muted` or `none`. */
    val feedback: String = "none",
    val saving: Boolean = false,
    val failed: Boolean = false,
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
                val verdict = api.verdict(videoId)
                _ui.update { it.copy(verdict = verdict, feedback = if (it.saving) it.feedback else verdict.feedback) }
            } catch (e: ApiException) {
                _ui.update { it.copy(error = e.message) }
            } catch (e: IOException) {
                _ui.update { it.copy(error = "The instance did not answer.") }
            }
        }
    }

    /**
     * Thumbs up, thumbs down and "less like this" are one state per video, stored
     * on the server (companion.md §2.3): a tap sets it at once, a tap on the one
     * already set takes it back. Shown as sent, put back if the server refused.
     */
    fun tap(state: String) {
        val now = _ui.value
        if (now.saving) return
        val target = if (now.feedback == state) "none" else state
        _ui.update { it.copy(feedback = target, saving = true, failed = false) }
        viewModelScope.launch {
            // Finished even if the screen closes meanwhile, so what is stored is what was shown.
            val ok = withContext(NonCancellable) { runCatching { api.feedback(videoId, target) }.isSuccess }
            _ui.update { if (ok) it.copy(saving = false) else it.copy(feedback = now.feedback, saving = false, failed = true) }
        }
    }

    /** `open` once per visit, when this page has settled with its verdict on screen. */
    fun shown() {
        if (opened || _ui.value.verdict == null) return
        opened = true
        quietly("open")
    }

    fun watched(offsetS: Double) = quietly("watch", offsetS.toInt())

    fun askedClaude() = quietly("ask_claude")

    private fun quietly(kind: String, offsetS: Int? = null) {
        viewModelScope.launch { runCatching { api.signal(kind, videoId, offsetS) } }
    }
}
