package dev.vidtheque.app.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
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
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

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
    return if (h > 0) "%d:%02d:%02d".format(h, m, r) else "%d:%02d".format(m, r)
}

/** YouTube serves stills for every public video; the server sends none. 4:3 with bars, so crop to 16:9. */
fun thumbnail(videoId: String): String = "https://i.ytimg.com/vi/$videoId/hqdefault.jpg"
