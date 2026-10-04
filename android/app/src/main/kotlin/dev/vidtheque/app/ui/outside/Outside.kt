package dev.vidtheque.app.ui.outside

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.rounded.ThumbDown
import androidx.compose.material.icons.rounded.ThumbUp
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FilledTonalIconToggleButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LargeFlexibleTopAppBar
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBarDefaults
import android.net.Uri
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.platform.LocalContext
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.vidtheque.app.ui.openLink
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import dev.vidtheque.app.data.OutsideFollow
import dev.vidtheque.app.data.OutsidePick
import dev.vidtheque.app.data.OutsideWeek
import dev.vidtheque.app.data.Speaker
import dev.vidtheque.app.ui.ScoreDial
import dev.vidtheque.app.ui.duration
import dev.vidtheque.app.ui.scoreWord
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

// Discovery outside the follows (companion.md §6.2): a small labelled dose, after the
// fitted week and outside its budget. Blue, the colour of "outside your profile".

/** The week's band: up to three picks and the speaker suggestion. */
@Composable
fun OutsideBand(
    outside: OutsideWeek,
    speakers: Map<Long, SpeakerUi>,
    onOpen: (OutsidePick) -> Unit,
    onFollowSpeaker: (Speaker) -> Unit,
    onDismissSpeaker: (Speaker) -> Unit,
    onLink: (String) -> Unit,
) {
    Column(Modifier.fillMaxWidth().padding(top = 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("From outside your follows", style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.onSurface)
        for (pick in outside.picks) PickCard(pick) { onOpen(pick) }
        outside.speaker?.let { speaker ->
            val state = speakers[speaker.id] ?: SpeakerUi(speaker.follow)
            if (!state.dismissed) SpeakerCard(speaker, state, { onFollowSpeaker(speaker) }, { onDismissSpeaker(speaker) }, onLink)
        }
    }
}

@Composable
fun Because(text: String) {
    Text("From outside, because of: $text", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.tertiary)
}

@Composable
private fun PickCard(pick: OutsidePick, onOpen: () -> Unit) {
    Card(
        onClick = onOpen,
        shape = MaterialTheme.shapes.large,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Because(pick.because)
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                pick.score?.let { ScoreDial(it, size = 36.dp) }
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(pick.title, style = MaterialTheme.typography.titleSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    Text(meta(pick), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            pick.reason?.let { Text(it, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant) }
        }
    }
}

private fun meta(pick: OutsidePick, channel: Boolean = true): String =
    listOfNotNull(pick.channel?.takeIf { channel }, pick.score?.let(::scoreWord), duration(pick.durationS)).joinToString(" · ")

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun SpeakerCard(speaker: Speaker, state: SpeakerUi, onFollow: () -> Unit, onDismiss: () -> Unit, onLink: (String) -> Unit) {
    Surface(shape = MaterialTheme.shapes.large, color = MaterialTheme.colorScheme.surfaceContainerLow, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text("A speaker from a talk you liked", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.tertiary)
            Text(speaker.name, style = MaterialTheme.typography.titleMediumEmphasized)
            Text(speaker.reason, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            for (talk in speaker.talks) {
                Text(
                    talk.title,
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.primary,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.clickable { onLink(talk.url) }.padding(vertical = 6.dp),
                )
            }
            FollowLine(state.follow)
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (speaker.channel != null && state.follow.state == "none") {
                    FilledTonalButton(onClick = onFollow, enabled = !state.busy) { Text("Follow for 14 days") }
                }
                TextButton(onClick = onDismiss, enabled = !state.busy) { Text("Not interested") }
            }
            if (state.failed) Text("Not saved. Try again.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
        }
    }
}

/** Where the 14-day follow stands; nothing before one was started. */
@Composable
fun FollowLine(follow: OutsideFollow) {
    val text = when (follow.state) {
        "trial" -> follow.until?.let { "On a 14-day follow until ${date(it)}." }
        "lasting" -> "You follow this channel."
        "ended" -> "The 14-day follow ended: nothing from it was liked."
        else -> null
    } ?: return
    Text(text, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

private fun date(epoch: Long): String =
    Instant.ofEpochSecond(epoch).atZone(ZoneId.systemDefault()).format(DateTimeFormatter.ofPattern("d MMM", Locale.getDefault()))

/** One pick's page: judged on its captions, never indexed; a thumbs up offers a 14-day follow. */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun OutsideContent(
    pick: OutsidePick,
    state: PickUi,
    onBack: () -> Unit,
    onThumb: (String) -> Unit,
    onFollow: () -> Unit,
    onMoment: (url: String, offsetS: Double) -> Unit,
) {
    val bar = TopAppBarDefaults.exitUntilCollapsedScrollBehavior()
    Scaffold(
        modifier = Modifier.nestedScroll(bar.nestedScrollConnection),
        topBar = {
            LargeFlexibleTopAppBar(
                title = { Text(pick.title, maxLines = 2, overflow = TextOverflow.Ellipsis) },
                subtitle = pick.channel?.let { { Text(it) } },
                navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Rounded.ArrowBack, contentDescription = "Back") } },
                scrollBehavior = bar,
            )
        },
        bottomBar = { Actions(state, onThumb, onFollow) },
    ) { padding ->
        LazyColumn(
            Modifier.fillMaxSize().padding(padding),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item(key = "head") {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Because(pick.because)
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
                        pick.score?.let { ScoreDial(it) }
                        Text(meta(pick, channel = false), style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    pick.reason?.let { Text(it, style = MaterialTheme.typography.bodyLarge) }
                    pick.summary?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
                }
            }
            items(pick.moments, key = { "moment-${it.offsetS}" }) { moment ->
                Card(
                    onClick = { onMoment(moment.url, moment.offsetS) },
                    shape = MaterialTheme.shapes.large,
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Row(Modifier.padding(16.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        Text(
                            duration(moment.offsetS) + (moment.endS?.let { "–" + duration(it) } ?: ""),
                            style = MaterialTheme.typography.labelLarge,
                            color = MaterialTheme.colorScheme.primary,
                        )
                        Text(moment.why, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
                    }
                }
            }
            item(key = "note") {
                Text(
                    "Judged on YouTube’s captions only. It is not in your library.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun Actions(state: PickUi, onThumb: (String) -> Unit, onFollow: () -> Unit) {
    Surface(color = MaterialTheme.colorScheme.surfaceContainer, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            val offer = state.offer
            if (offer != null) {
                Text(
                    "Try ${offer.channel ?: "this channel"} for ${offer.days} days? It ends by itself unless you like something from it.",
                    style = MaterialTheme.typography.bodyMedium,
                )
                FilledTonalButton(onClick = onFollow, enabled = !state.busy) { Text("Follow for ${offer.days} days") }
            } else {
                FollowLine(state.follow)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                FilledTonalIconToggleButton(checked = state.feedback == "up", onCheckedChange = { onThumb("up") }, enabled = !state.busy) {
                    Icon(Icons.Rounded.ThumbUp, contentDescription = "Thumbs up")
                }
                FilledTonalIconToggleButton(checked = state.feedback == "down", onCheckedChange = { onThumb("down") }, enabled = !state.busy) {
                    Icon(Icons.Rounded.ThumbDown, contentDescription = "Thumbs down")
                }
            }
            if (state.failed) Text("Not saved. Tap again to retry.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
        }
    }
}

@Composable
fun OutsideScreen(pick: OutsidePick, onBack: () -> Unit) {
    val model: OutsideViewModel = hiltViewModel()
    val picks by model.picks.collectAsStateWithLifecycle()
    val context = LocalContext.current
    OutsideContent(
        pick = pick,
        state = picks[pick.id] ?: PickUi(pick.feedback, pick.follow),
        onBack = onBack,
        onThumb = { model.thumb(pick, it) },
        onFollow = { model.follow(pick) },
        onMoment = { url, offset -> if (context.openLink(Uri.parse(url))) model.watched(pick, offset) },
    )
}
