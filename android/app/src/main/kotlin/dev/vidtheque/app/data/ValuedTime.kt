package dev.vidtheque.app.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// The weekly ledger (dashboard.md §25.12) and a share's answer (§25.11).

@Serializable
data class Hits(val kept: Int, val offered: Int, val rate: Double? = null)

@Serializable
data class Regret(val down: Int, val watched: Int, val rate: Double? = null)

@Serializable
data class Misses(val count: Int, val pending: Int = 0, val shared: Int = 0)

@Serializable
data class Week(val start: Long, val current: Boolean = false, val hits: Hits, val regret: Regret, val misses: Misses)

@Serializable
data class ValuedTime(@SerialName("regret_target") val regretTarget: Double = 0.1, val weeks: List<Week> = emptyList())

/** [miss] is null while the video waits for its verdict; [why] says why in a few words. */
@Serializable
data class Shared(@SerialName("video_id") val videoId: String, val miss: Boolean? = null, val why: String = "")

@Serializable
internal data class Recorded(@SerialName("signal_id") val signalId: Long? = null)
