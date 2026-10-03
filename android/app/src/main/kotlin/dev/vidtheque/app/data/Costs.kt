package dev.vidtheque.app.data

import java.util.Locale
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// What the model calls cost (dashboard.md §25.8): micro-USD at list price, null when unknown.

@Serializable
data class CostWindow(
    val calls: Int,
    @SerialName("unpriced_calls") val unpricedCalls: Int = 0,
    @SerialName("cost_micro_usd") val costMicroUsd: Long? = null,
    val verdicts: Int = 0,
    @SerialName("per_verdict_micro_usd") val perVerdictMicroUsd: Long? = null,
)

@Serializable
data class CostWindows(val month: CostWindow)

@Serializable
data class Costs(val windows: CostWindows)

/** `$12.40` from a dollar up, `$0.0412` below, as the console prints it; unknown is a dash, never $0. */
fun usd(micro: Long?): String {
    if (micro == null) return "—"
    val dollars = micro / 1_000_000.0
    return String.format(Locale.US, if (kotlin.math.abs(dollars) >= 1) "$%.2f" else "$%.4f", dollars)
}
