package dev.vidtheque.app.ui.collection

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.assisted.Assisted
import dagger.assisted.AssistedFactory
import dagger.assisted.AssistedInject
import dagger.hilt.android.lifecycle.HiltViewModel
import dev.vidtheque.app.data.Api
import dev.vidtheque.app.data.ApiException
import dev.vidtheque.app.data.CollectionMoment
import dev.vidtheque.app.data.MomentCollection
import dev.vidtheque.app.data.WatchClock
import java.io.IOException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class CollectionUi(val collection: MomentCollection? = null, val error: String? = null)

/** One entry's moments, best first (companion.md §6.2). */
@HiltViewModel(assistedFactory = CollectionViewModel.Factory::class)
class CollectionViewModel @AssistedInject constructor(
    private val api: Api,
    private val watchClock: WatchClock,
    @Assisted private val entryId: Long,
) : ViewModel() {
    @AssistedFactory
    interface Factory {
        fun create(entryId: Long): CollectionViewModel
    }

    private val _ui = MutableStateFlow(CollectionUi())
    val ui: StateFlow<CollectionUi> = _ui.asStateFlow()

    init {
        load()
    }

    fun load() {
        _ui.update { it.copy(error = null) }
        viewModelScope.launch {
            try {
                val collection = api.collection(entryId)
                _ui.update { it.copy(collection = collection) }
            } catch (e: ApiException) {
                _ui.update { it.copy(error = e.message) }
            } catch (_: IOException) {
                _ui.update { it.copy(error = "The instance did not answer.") }
            }
        }
    }

    /** The link was handed to YouTube: the same `watch` and clock as the video screen's. */
    fun watched(moment: CollectionMoment) = watchClock.handOff(moment.video.videoId, moment.startS.toInt())
}
