package dev.vidtheque.app.ui.brief

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.vidtheque.app.data.Match
import java.util.Locale

/** The strongest "less of this" match: what sank a skipped video. */
fun sunkBy(matches: List<Match>): Match? = matches.filter { it.direction == "down" }.maxByOrNull { it.strength }

@Composable
fun SunkBy(match: Match?, modifier: Modifier = Modifier) {
    if (match == null) return
    Text("Sunk by “${match.text}”", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.error, modifier = modifier)
}

/** Under a skipped row in the feed: what sank it, and "I'd watch this". */
@Composable
fun SkipFix(videoId: String, matches: List<Match>) {
    Column(Modifier.padding(start = 12.dp, end = 12.dp, top = 4.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        SunkBy(sunkBy(matches))
        WouldWatch(videoId)
    }
}

/** The feed's skipped row: "I'd watch this", then the reweight it proposes. */
@Composable
fun WouldWatch(videoId: String, modifier: Modifier = Modifier) {
    val model: SkipViewModel = hiltViewModel()
    val skips by model.skips.collectAsStateWithLifecycle()
    val state = skips[videoId]
    Column(modifier) {
        if (state?.answer == null) {
            OutlinedButton(onClick = { model.answer(videoId, "wrong", "row") }) { Text("I’d watch this") }
        } else {
            Outcome(state) { proposal -> model.ease(videoId, proposal) }
        }
        state?.error?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error) }
    }
}

/** The brief's audit: "would you have watched it?", yes or no. */
@Composable
fun AuditAnswer(state: SkipState, stored: String?, onAnswer: (String) -> Unit, onEase: (dev.vidtheque.app.data.Proposal) -> Unit) {
    val shown = state.answer ?: stored
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Would you have watched it?", style = MaterialTheme.typography.labelLarge, modifier = Modifier.weight(1f))
            for ((value, label) in listOf("wrong" to "Yes", "right" to "No")) {
                if (shown == value) FilledTonalButton(onClick = {}) { Text(label) } else OutlinedButton(onClick = { onAnswer(value) }) { Text(label) }
            }
        }
        if (state.answer == "wrong") Outcome(state, onEase)
        state.error?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error) }
    }
}

@Composable
private fun Outcome(state: SkipState, onEase: (dev.vidtheque.app.data.Proposal) -> Unit) {
    val proposal = state.proposal
    when {
        proposal == null -> Text("Noted, as a thumb up.", style = MaterialTheme.typography.bodyMedium)
        state.eased -> Text("“${proposal.text}” is now ${signed(proposal.to)}.", style = MaterialTheme.typography.bodyMedium)
        else -> FlowRow(verticalArrangement = Arrangement.Center, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(
                "Ease “${proposal.text}” from ${signed(proposal.weight)} to ${signed(proposal.to)}?",
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(vertical = 12.dp),
            )
            TextButton(onClick = { onEase(proposal) }) { Text("Ease it") }
        }
    }
}

fun signed(weight: Double): String {
    val fixed = String.format(Locale.ROOT, "%.1f", kotlin.math.abs(weight))
    return if (fixed == "0.0") fixed else (if (weight < 0) "−" else "+") + fixed
}
