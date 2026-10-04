package dev.vidtheque.app.ui.profile

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dev.vidtheque.app.BuildConfig
import dev.vidtheque.app.auth.Instance
import dev.vidtheque.app.data.Api
import dev.vidtheque.app.data.ApiException
import dev.vidtheque.app.data.CollectionSummary
import dev.vidtheque.app.data.Profile
import dev.vidtheque.app.data.ProfileEvent
import dev.vidtheque.app.push.Push
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.io.IOException
import javax.inject.Inject

data class ProfileUi(
    val profile: Profile? = null,
    /** Each wanted entry's moments by entry id (companion.md §6.2); the profile draws without them. */
    val collections: Map<Long, CollectionSummary> = emptyMap(),
    /** Every history page read so far; a write starts over from the first. */
    val events: List<ProfileEvent> = emptyList(),
    val nextBefore: Long? = null,
    /** One request at a time: each write answers the whole profile, so two in flight could land out of order. */
    val busy: Boolean = false,
    val error: String? = null,
)

@HiltViewModel
class ProfileViewModel @Inject constructor(private val api: Api, private val push: Push, private val instance: Instance) : ViewModel() {
    val pushAvailable: Boolean get() = push.available
    val host: String get() = instance.host

    /** Set when this build has push, but for another instance than this one. */
    val pushOnlyAt: String? get() = Instance.hostOf(BuildConfig.PUSH_INSTANCE).takeIf { push.built && !push.available }
    val pushOn: StateFlow<Boolean> = push.on.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), false)

    /** On after the system granted the permission; off forgets this phone on the instance. */
    fun setPush(on: Boolean) = exclusive { if (on) push.enable() else push.disable() }

    private val _ui = MutableStateFlow(ProfileUi())
    val ui: StateFlow<ProfileUi> = _ui.asStateFlow()

    init {
        load()
    }

    fun load() = run { api.profile() }

    /** Apart from the profile: a collection that fails to load costs only its link. */
    private fun collect() {
        viewModelScope.launch {
            val found = runCatching { api.collections().collections }.getOrNull() ?: return@launch
            _ui.update { ui -> ui.copy(collections = found.associateBy { it.entryId }) }
        }
    }

    fun older() {
        val before = _ui.value.nextBefore ?: return
        exclusive {
            val page = api.profile(before)
            _ui.update { it.copy(events = it.events + page.history.events, nextBefore = page.history.nextBefore.takeIf { page.history.hasMore }) }
        }
    }

    fun drop(entryId: Long) = run { api.drop(entryId) }

    fun revert(eventId: Long) = run { api.revert(eventId) }

    /** Every write answers the whole profile, so the screen redraws from the server's word,
     *  and the collections, which follow the entries, are read again. */
    private fun run(call: suspend () -> Profile) = exclusive {
        val profile = call()
        _ui.update {
            it.copy(profile = profile, events = profile.history.events, nextBefore = profile.history.nextBefore.takeIf { profile.history.hasMore })
        }
        collect()
    }

    private fun exclusive(block: suspend () -> Unit) {
        if (_ui.value.busy) return
        _ui.update { it.copy(error = null, busy = true) }
        viewModelScope.launch {
            guard(block)
            _ui.update { it.copy(busy = false) }
        }
    }

    private suspend fun guard(block: suspend () -> Unit) {
        try {
            block()
        } catch (e: ApiException) {
            _ui.update { it.copy(error = e.message) }
        } catch (e: IOException) {
            _ui.update { it.copy(error = "The instance did not answer.") }
        } catch (e: Exception) {
            // Firebase's own failures (no Play services, no network to FCM) land here.
            if (e is kotlinx.coroutines.CancellationException) throw e
            _ui.update { it.copy(error = "Notifications could not be set up: ${e.message ?: e.javaClass.simpleName}") }
        }
    }
}
