package dev.vidtheque.app.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// Discovery outside the follows (dashboard.md §27, companion.md §6.2).

/** `none`, `trial` (until [until]), `lasting`, or `ended` for a trial nothing from was liked. */
@Serializable
data class OutsideFollow(val state: String = "none", val until: Long? = null)

@Serializable
data class OutsideMoment(
    @SerialName("offset_s") val offsetS: Double,
    @SerialName("end_s") val endS: Double? = null,
    val why: String,
    val url: String,
)

@Serializable
data class OutsidePick(
    val id: Long,
    @SerialName("video_id") val videoId: String,
    val url: String,
    val title: String,
    val channel: String? = null,
    @SerialName("duration_s") val durationS: Double = 0.0,
    @SerialName("published_at") val publishedAt: Long? = null,
    /** The profile entry it was scouted for. */
    val because: String,
    val score: Int? = null,
    val reason: String? = null,
    val summary: String? = null,
    val moments: List<OutsideMoment> = emptyList(),
    val feedback: String = "none",
    val follow: OutsideFollow = OutsideFollow(),
)

@Serializable
data class SpeakerChannel(val name: String? = null, val url: String)

@Serializable
data class SpeakerTalk(@SerialName("video_id") val videoId: String, val title: String, val channel: String? = null, val url: String)

@Serializable
data class Speaker(
    val id: Long,
    val name: String,
    val reason: String,
    val channel: SpeakerChannel? = null,
    val talks: List<SpeakerTalk> = emptyList(),
    val follow: OutsideFollow = OutsideFollow(),
)

@Serializable
data class OutsideWeek(val week: String, val picks: List<OutsidePick> = emptyList(), val speaker: Speaker? = null) {
    val empty: Boolean get() = picks.isEmpty() && speaker == null
}

@Serializable
data class TrialOffer(val channel: String? = null, val url: String, val days: Int = 14)

@Serializable
data class OutsideFeedbackStored(val id: Long, val state: String, val offer: TrialOffer? = null)

@Serializable
data class TrialStarted(@SerialName("trial_until") val trialUntil: Long? = null, val already: Boolean = false)
