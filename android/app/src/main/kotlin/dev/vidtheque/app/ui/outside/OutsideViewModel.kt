package dev.vidtheque.app.ui.outside

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dev.vidtheque.app.data.Api
import dev.vidtheque.app.data.ApiException
import dev.vidtheque.app.data.OutsideFollow
import dev.vidtheque.app.data.OutsidePick
import dev.vidtheque.app.data.Speaker
import dev.vidtheque.app.data.TrialOffer
import dev.vidtheque.app.data.WatchClock
import java.io.IOException
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** A pick's stored thumb, its channel's follow, and the 14-day follow on offer after a thumbs up. */
data class PickUi(
    val feedback: String = "none",
    val follow: OutsideFollow = OutsideFollow(),
    val offer: TrialOffer? = null,
    val busy: Boolean = false,
    val failed: Boolean = false,
)

data class SpeakerUi(val follow: OutsideFollow = OutsideFollow(), val dismissed: Boolean = false, val busy: Boolean = false, val failed: Boolean = false)

/** The writes of companion.md §6.2, with the server's answer as the state (dashboard.md §27). */
@HiltViewModel
class OutsideViewModel @Inject constructor(private val api: Api, private val watchClock: WatchClock) : ViewModel() {
    private val _picks = MutableStateFlow<Map<Long, PickUi>>(emptyMap())
    val picks: StateFlow<Map<Long, PickUi>> = _picks.asStateFlow()
    private val _speakers = MutableStateFlow<Map<Long, SpeakerUi>>(emptyMap())
    val speakers: StateFlow<Map<Long, SpeakerUi>> = _speakers.asStateFlow()

    fun state(pick: OutsidePick): PickUi = _picks.value[pick.id] ?: PickUi(pick.feedback, pick.follow)

    private fun pick(pick: OutsidePick, change: (PickUi) -> PickUi) =
        _picks.update { it + (pick.id to change(it[pick.id] ?: PickUi(pick.feedback, pick.follow))) }

    private fun speaker(speaker: Speaker, change: (SpeakerUi) -> SpeakerUi) =
        _speakers.update { it + (speaker.id to change(it[speaker.id] ?: SpeakerUi(speaker.follow))) }

    /** A tap on the thumb already set takes it back; a refusal puts it back. */
    fun thumb(target: OutsidePick, thumb: String) {
        val before = state(target)
        if (before.busy) return
        val next = if (before.feedback == thumb) "none" else thumb
        pick(target) { it.copy(feedback = next, busy = true, failed = false) }
        viewModelScope.launch {
            try {
                val stored = api.outsideFeedback(target.id, next)
                pick(target) { it.copy(offer = stored.offer, busy = false) }
            } catch (_: ApiException) {
                pick(target) { before.copy(failed = true) }
            } catch (_: IOException) {
                pick(target) { before.copy(failed = true) }
            }
        }
    }

    fun follow(target: OutsidePick) {
        pick(target) { it.copy(busy = true, failed = false) }
        viewModelScope.launch {
            try {
                val started = api.trialFollow("pick", target.id)
                pick(target) { it.copy(follow = OutsideFollow("trial", started.trialUntil), offer = null, busy = false) }
            } catch (_: ApiException) {
                pick(target) { it.copy(busy = false, failed = true) }
            } catch (_: IOException) {
                pick(target) { it.copy(busy = false, failed = true) }
            }
        }
    }

    fun follow(target: Speaker) {
        speaker(target) { it.copy(busy = true, failed = false) }
        viewModelScope.launch {
            try {
                val started = api.trialFollow("speaker", target.id)
                speaker(target) { it.copy(follow = OutsideFollow("trial", started.trialUntil), busy = false) }
            } catch (_: ApiException) {
                speaker(target) { it.copy(busy = false, failed = true) }
            } catch (_: IOException) {
                speaker(target) { it.copy(busy = false, failed = true) }
            }
        }
    }

    fun dismiss(target: Speaker) {
        speaker(target) { it.copy(busy = true, failed = false) }
        viewModelScope.launch {
            try {
                api.dismissSpeaker(target.id)
                speaker(target) { it.copy(dismissed = true, busy = false) }
            } catch (_: ApiException) {
                speaker(target) { it.copy(busy = false, failed = true) }
            } catch (_: IOException) {
                speaker(target) { it.copy(busy = false, failed = true) }
            }
        }
    }

    /** The moment was handed to YouTube; the return measures the watch (§27.4). */
    fun watched(target: OutsidePick, offsetS: Double) = watchClock.handOffOutside(target.id, offsetS.toInt())
}
