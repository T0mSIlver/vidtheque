package dev.vidtheque.app.ui

import android.text.format.DateFormat
import android.text.format.DateUtils
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.KeyboardArrowDown
import androidx.compose.material.icons.rounded.KeyboardArrowUp
import androidx.compose.material.icons.rounded.KeyboardDoubleArrowDown
import androidx.compose.material.icons.rounded.KeyboardDoubleArrowUp
import androidx.compose.material3.Icon
import androidx.compose.material3.Surface
import androidx.compose.material3.CircularWavyProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.WavyProgressIndicatorDefaults
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import dev.vidtheque.app.data.Match
import dev.vidtheque.app.ui.theme.LocalTones
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/** Each score is an action (companion.md §3.1), and it always prints its word. */
fun scoreWord(score: Int): String = when (score) {
    3 -> "Watch it whole"
    2 -> "Watch the moments"
    1 -> "Summary is enough"
    else -> "Skip"
}

@Composable
fun scoreColor(score: Int): Color = when (score) {
    3 -> MaterialTheme.colorScheme.primary
    2 -> MaterialTheme.colorScheme.secondary
    else -> MaterialTheme.colorScheme.outline
}

/**
 * The score as a dial: the ring fills to score/3. A dial earns its place here
 * because the score is the one number the feed asks you to act on (DESIGN.md).
 */
@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun ScoreDial(score: Int, modifier: Modifier = Modifier, size: Dp = 44.dp) {
    Box(
        modifier.size(size).clearAndSetSemantics { contentDescription = "Score $score of 3, ${scoreWord(score)}" },
        contentAlignment = Alignment.Center,
    ) {
        CircularWavyProgressIndicator(
            progress = { score / 3f },
            modifier = Modifier.size(size),
            color = scoreColor(score),
            trackColor = MaterialTheme.colorScheme.surfaceContainerHighest,
            // Still: the wave is the shape of the dial, not a sign of work.
            waveSpeed = 0.dp,
            amplitude = { if (it >= 1f) 0f else WavyProgressIndicatorDefaults.indicatorAmplitude(it) },
        )
        Text(score.toString(), style = MaterialTheme.typography.titleMediumEmphasized, color = scoreColor(score))
    }
}

fun duration(seconds: Double): String {
    val s = seconds.toLong()
    val h = s / 3600
    val m = (s % 3600) / 60
    val r = s % 60
    return if (h > 0) "%d:%02d:%02d".format(java.util.Locale.ROOT, h, m, r) else "%d:%02d".format(java.util.Locale.ROOT, m, r)
}

/** "6 of 42 min" when the moments have spans and cover part of the video, else its length. */
fun asked(momentsS: Double?, durationS: Double): String {
    if (momentsS == null || momentsS <= 0 || durationS <= 0) return duration(durationS)
    val whole = maxOf(1L, Math.round(durationS / 60))
    // A short moment still costs a minute; never more than the video.
    val part = minOf(whole, maxOf(1L, Math.round(momentsS / 60)))
    // Non-breaking, so a wrapped meta line never strands "min".
    return "$part\u00A0of\u00A0$whole\u00A0min"
}

/** YouTube serves stills for every public video; the server sends none. 4:3 with bars, so crop to 16:9. */
fun thumbnail(videoId: String): String = "https://i.ytimg.com/vi/$videoId/hqdefault.jpg"

/**
 * A profile weight as a dial: the ring fills to |weight|, gold for more of this,
 * the error tone for less. The sign prints in the middle, so colour is never alone.
 */
@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun WeightDial(weight: Double, modifier: Modifier = Modifier, size: Dp = 52.dp) {
    val tone = if (weight < 0) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary
    Box(modifier.size(size).clearAndSetSemantics { contentDescription = "Weight ${dev.vidtheque.app.data.signed(weight)}" }, contentAlignment = Alignment.Center) {
        CircularWavyProgressIndicator(
            progress = { kotlin.math.abs(weight).toFloat() },
            modifier = Modifier.size(size),
            color = tone,
            trackColor = MaterialTheme.colorScheme.surfaceContainerHighest,
            waveSpeed = 0.dp,
            amplitude = { if (it >= 1f) 0f else WavyProgressIndicatorDefaults.indicatorAmplitude(it) },
        )
        Text(dev.vidtheque.app.data.signed(weight), style = MaterialTheme.typography.labelMediumEmphasized, color = tone)
    }
}


private const val WEEK_S = 7 * 24 * 3600L

/** A date as people say it: "just now", "2 days ago" within a week, the date beyond (the year only when it is not this one). */
fun dated(at: Long, now: Long = System.currentTimeMillis() / 1000, zone: ZoneId = ZoneId.systemDefault(), locale: Locale = Locale.getDefault()): String {
    if (now - at in 0..59) return "just now"
    if (now - at < WEEK_S) return DateUtils.getRelativeTimeSpanString(at * 1000, now * 1000, DateUtils.MINUTE_IN_MILLIS).toString()
    val date = Instant.ofEpochSecond(at).atZone(zone).toLocalDate()
    val skeleton = if (date.year == Instant.ofEpochSecond(now).atZone(zone).year) "MMMd" else "yMMMd"
    return DateTimeFormatter.ofPattern(DateFormat.getBestDateTimePattern(locale, skeleton), locale).format(date)
}

/**
 * The profile entries a verdict matched, as chips: green for an entry you want more
 * of, red for one you want less of, one chevron when the video touches it, two when
 * it is central. The arrow carries the direction, so colour is never alone.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun Matches(matches: List<Match>, modifier: Modifier = Modifier, maxLines: Int = Int.MAX_VALUE) {
    FlowRow(
        modifier,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
        maxLines = maxLines,
    ) {
        matches.forEach { MatchChip(it) }
    }
}

@Composable
private fun MatchChip(match: Match) {
    val tones = LocalTones.current
    val up = match.direction == "up"
    val strong = match.strength >= 2
    val icon = when {
        up && strong -> Icons.Rounded.KeyboardDoubleArrowUp
        up -> Icons.Rounded.KeyboardArrowUp
        strong -> Icons.Rounded.KeyboardDoubleArrowDown
        else -> Icons.Rounded.KeyboardArrowDown
    }
    val said = (if (strong) "Strongly matches " else "Matches ") + (if (up) "an interest: " else "something you avoid: ") + match.text
    Surface(
        shape = MaterialTheme.shapes.small,
        color = if (up) tones.up else tones.down,
        contentColor = if (up) tones.onUp else tones.onDown,
        modifier = Modifier.clearAndSetSemantics { contentDescription = said },
    ) {
        Row(Modifier.padding(start = 4.dp, end = 10.dp, top = 4.dp, bottom = 4.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(2.dp)) {
            Icon(icon, contentDescription = null, modifier = Modifier.size(18.dp))
            // Two lines hold any entry under the 32-character cap; one written before it is cut there.
            Text(match.text, style = MaterialTheme.typography.labelLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
        }
    }
}
