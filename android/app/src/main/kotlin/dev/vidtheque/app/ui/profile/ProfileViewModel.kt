package dev.vidtheque.app.ui.profile

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dev.vidtheque.app.data.Api
import dev.vidtheque.app.data.ApiException
import dev.vidtheque.app.data.Profile
import dev.vidtheque.app.data.ProfileEvent
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.io.IOException
import javax.inject.Inject

data class ProfileUi(
    val profile: Profile? = null,
    /** Every history page read so far; a write starts over from the first. */
    val events: List<ProfileEvent> = emptyList(),
    val nextBefore: Long? = null,
    val busy: Set<Long> = emptySet(),
    val error: String? = null,
)

@HiltViewModel
class ProfileViewModel @Inject constructor(private val api: Api) : ViewModel() {
    private val _ui = MutableStateFlow(ProfileUi())
    val ui: StateFlow<ProfileUi> = _ui.asStateFlow()

    init {
        load()
    }

    fun load() = run { api.profile() }

    fun older() {
        val before = _ui.value.nextBefore ?: return
        viewModelScope.launch {
            guard {
                val page = api.profile(before)
                _ui.update { it.copy(events = it.events + page.history.events, nextBefore = page.history.nextBefore.takeIf { page.history.hasMore }) }
            }
        }
    }

    fun drop(entryId: Long) = run(busy = entryId) { api.drop(entryId) }

    fun revert(eventId: Long) = run(busy = eventId) { api.revert(eventId) }

    /** Every write answers the whole profile, so the screen redraws from the server's word. */
    private fun run(busy: Long? = null, call: suspend () -> Profile) {
        if (busy != null && busy in _ui.value.busy) return
        _ui.update { it.copy(error = null, busy = it.busy + listOfNotNull(busy)) }
        viewModelScope.launch {
            guard {
                val profile = call()
                _ui.update {
                    it.copy(profile = profile, events = profile.history.events, nextBefore = profile.history.nextBefore.takeIf { profile.history.hasMore })
                }
            }
            if (busy != null) _ui.update { it.copy(busy = it.busy - busy) }
        }
    }

    private suspend fun guard(block: suspend () -> Unit) {
        try {
            block()
        } catch (e: ApiException) {
            _ui.update { it.copy(error = e.message) }
        } catch (e: IOException) {
            _ui.update { it.copy(error = "The instance did not answer.") }
        }
    }
}
