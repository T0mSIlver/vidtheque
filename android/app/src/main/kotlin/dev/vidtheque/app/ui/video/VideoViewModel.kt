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
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.io.IOException

enum class Sent { Sending, Done, Failed }

data class VideoUi(
    val verdict: Verdict? = null,
    val error: String? = null,
    val sent: Map<String, Sent> = emptyMap(),
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

    /** Thumbs and mute: one signal each per visit, retried by tapping again if it failed. */
    fun send(kind: String) {
        val now = _ui.value.sent[kind]
        if (now == Sent.Sending || now == Sent.Done) return
        mark(kind, Sent.Sending)
        viewModelScope.launch {
            mark(kind, if (runCatching { api.signal(kind, videoId) }.isSuccess) Sent.Done else Sent.Failed)
        }
    }

    fun watched(offsetS: Double) = quietly("watch", offsetS.toInt())

    fun askedClaude() = quietly("ask_claude")

    private fun quietly(kind: String, offsetS: Int? = null) {
        viewModelScope.launch { runCatching { api.signal(kind, videoId, offsetS) } }
    }

    private fun mark(kind: String, state: Sent) = _ui.update { it.copy(sent = it.sent + (kind to state)) }
}
