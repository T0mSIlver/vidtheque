package dev.vidtheque.app.ui.brief

import android.net.Uri
import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.rounded.ExpandLess
import androidx.compose.material.icons.rounded.ExpandMore
import androidx.compose.material.icons.rounded.Undo
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LargeFlexibleTopAppBar
import androidx.compose.material3.LoadingIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.vidtheque.app.data.Brief
import dev.vidtheque.app.data.ChannelCard
import dev.vidtheque.app.data.Change
import dev.vidtheque.app.data.OutsidePick
import dev.vidtheque.app.data.OutsideWeek
import dev.vidtheque.app.data.Pick
import dev.vidtheque.app.data.Proposal
import dev.vidtheque.app.data.Receipt
import dev.vidtheque.app.ui.ScoreDial
import dev.vidtheque.app.ui.duration
import dev.vidtheque.app.ui.openLink
import dev.vidtheque.app.ui.outside.OutsideBand
import dev.vidtheque.app.ui.outside.OutsideViewModel
import dev.vidtheque.app.ui.profile.percent
import dev.vidtheque.app.ui.scoreWord

@Composable
fun BriefScreen(
    onBack: () -> Unit,
    onOpen: (videoId: String, title: String, channel: String) -> Unit,
    onOutside: (OutsidePick) -> Unit = {},
) {
    val model: BriefViewModel = hiltViewModel()
    val outsideModel: OutsideViewModel = hiltViewModel()
    val speakers by outsideModel.speakers.collectAsStateWithLifecycle()
    val skipModel: SkipViewModel = hiltViewModel()
    val ui by model.ui.collectAsStateWithLifecycle()
    val skips by skipModel.skips.collectAsStateWithLifecycle()
    val context = LocalContext.current
    BriefContent(
        ui = ui,
        skips = skips,
        onBack = onBack,
        onRetry = { model.load() },
        onEarlier = { model.load(it) },
        onOpen = onOpen,
        onLink = { context.openLink(Uri.parse(it)) },
        onCheckin = model::checkin,
        onRevert = model::revert,
        onPause = model::pause,
        onAudit = { id, answer -> skipModel.answer(id, answer, "audit") },
        onEase = skipModel::ease,
        outside = { week ->
            OutsideBand(week, speakers, onOutside, outsideModel::follow, outsideModel::dismiss) { context.openLink(Uri.parse(it)) }
        },
    )
}

/** The brief, about one phone screen: picks and check-in open, the rest a tap away. */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun BriefContent(
    ui: BriefUi,
    skips: Map<String, SkipState>,
    onBack: () -> Unit,
    onRetry: () -> Unit,
    onEarlier: (String) -> Unit,
    onOpen: (videoId: String, title: String, channel: String) -> Unit,
    onLink: (String) -> Unit,
    onCheckin: (Int, String) -> Unit,
    onRevert: (Long) -> Unit,
    onPause: (String) -> Unit,
    onAudit: (String, String) -> Unit,
    onEase: (String, Proposal) -> Unit,
    outside: @Composable (OutsideWeek) -> Unit = {},
) {
    val bar = TopAppBarDefaults.exitUntilCollapsedScrollBehavior()
    val brief = ui.brief
    Scaffold(
        modifier = Modifier.nestedScroll(bar.nestedScrollConnection),
        topBar = {
            LargeFlexibleTopAppBar(
                title = { Text("Your week") },
                subtitle = brief?.let { { Text("Week of ${weekOf(it.week)}") } },
                navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Rounded.ArrowBack, contentDescription = "Back") } },
                actions = { brief?.previousWeek?.let { week -> TextButton(onClick = { onEarlier(week) }, enabled = !ui.busy) { Text("Earlier") } } },
                scrollBehavior = bar,
            )
        },
    ) { padding ->
        if (brief == null) {
            Column(Modifier.fillMaxSize().padding(padding).padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
                when {
                    ui.error != null -> {
                        Text(ui.error, style = MaterialTheme.typography.bodyLarge)
                        TextButton(onClick = onRetry) { Text("Try again") }
                    }
                    else -> LoadingIndicator()
                }
            }
            return@Scaffold
        }
        LazyColumn(
            Modifier.fillMaxSize().padding(padding),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            ui.error?.let { item { Text(it, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.error) } }
            if (brief.picks.isEmpty()) item { Text("Nothing scored worth your time this week.", style = MaterialTheme.typography.bodyLarge) }
            items(brief.picks, key = { "pick-${it.videoId}" }) { pick -> PickCard(pick, onOpen, onLink) }
            brief.ledger?.weeks?.firstOrNull()?.let { week ->
                item(key = "ledger") {
                    Text(
                        "Against YouTube: ${percent(week.hits.rate)} hit rate · ${percent(week.regret.rate)} regret · ${week.misses.count} " + if (week.misses.count == 1) "miss" else "misses",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(horizontal = 4.dp),
                    )
                }
            }
            brief.outside?.takeIf { !it.empty }?.let { week -> item(key = "outside") { outside(week) } }
            item(key = "checkin") { CheckinCard(brief, ui, onCheckin) }
            briefFolds(brief, skips, ui.busy, onLink, onRevert, onPause, onAudit, onEase)
        }
    }
}

private fun LazyListScope.briefFolds(
    brief: Brief,
    skips: Map<String, SkipState>,
    busy: Boolean,
    onLink: (String) -> Unit,
    onRevert: (Long) -> Unit,
    onPause: (String) -> Unit,
    onAudit: (String, String) -> Unit,
    onEase: (String, Proposal) -> Unit,
) {
    item(key = "said") {
        Fold("What speakers said", "${brief.said.size}") {
            if (brief.said.isEmpty()) Text("Nothing this week" + (brief.saidNote?.let { ": $it." } ?: "."), style = MaterialTheme.typography.bodyMedium)
            for (topic in brief.said) {
                Text(topic.text, style = MaterialTheme.typography.titleSmall)
                for (point in topic.points) Said(point.said.orEmpty(), point, onLink)
                topic.disagreement?.let { d ->
                    Text("They disagree: ${d.about}", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.tertiary)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) { d.sides.forEach { side -> ReceiptLink(side, onLink) } }
                }
            }
        }
    }
    val flagged = brief.channels.count { it.suggestPause }
    item(key = "channels") {
        Fold("Channels", if (flagged > 0) "$flagged to review" else "${brief.channels.size} followed") {
            for (channel in brief.channels) ChannelRow(channel, busy) { onPause(channel.slug) }
        }
    }
    item(key = "changes") {
        Fold("Profile changes", "${brief.profileChanges.size}") {
            if (brief.profileChanges.isEmpty()) Text("The nightly update changed nothing this week.", style = MaterialTheme.typography.bodyMedium)
            for (change in brief.profileChanges) ChangeRow(change, busy) { onRevert(change.eventId) }
        }
    }
    item(key = "audit") {
        Fold("Skip audit", "${brief.audit.size}") {
            for (video in brief.audit) {
                Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    Text(video.title, style = MaterialTheme.typography.titleSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    Text(video.reason, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    SunkBy(video.sunkBy)
                    AuditAnswer(skips[video.videoId] ?: SkipState(), video.answer, { onAudit(video.videoId, it) }) { onEase(video.videoId, it) }
                }
            }
        }
    }
}

@Composable
private fun PickCard(pick: Pick, onOpen: (String, String, String) -> Unit, onLink: (String) -> Unit) {
    Card(
        onClick = { onOpen(pick.videoId, pick.title, pick.channel.orEmpty()) },
        shape = MaterialTheme.shapes.large,
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(Modifier.padding(12.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            ScoreDial(pick.score, size = 36.dp)
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(pick.title, style = MaterialTheme.typography.titleSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text(listOfNotNull(pick.channel, scoreWord(pick.score), duration(pick.durationS)).joinToString(" · "), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                for (moment in pick.moments.take(2)) {
                    Text(
                        "${duration(moment.offsetS)}  ${moment.why}",
                        style = MaterialTheme.typography.labelLarge,
                        color = MaterialTheme.colorScheme.primary,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.clickable { onLink(moment.url) }.padding(vertical = 6.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun CheckinCard(brief: Brief, ui: BriefUi, onCheckin: (Int, String) -> Unit) {
    var rating by rememberSaveable(brief.week) { mutableStateOf(brief.checkin?.rating) }
    var missing by rememberSaveable(brief.week) { mutableStateOf(brief.checkin?.missing.orEmpty()) }
    Surface(shape = MaterialTheme.shapes.large, color = MaterialTheme.colorScheme.surfaceContainerLow, modifier = Modifier.fillMaxWidth().animateContentSize()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Was last week’s feed worth the time?", style = MaterialTheme.typography.titleSmall)
            SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                for (value in 1..5) {
                    SegmentedButton(
                        selected = rating == value,
                        onClick = {
                            rating = value
                            onCheckin(value, missing)
                        },
                        shape = SegmentedButtonDefaults.itemShape(value - 1, 5),
                        icon = {},
                    ) { Text("$value") }
                }
            }
            if (rating != null) {
                OutlinedTextField(
                    value = missing,
                    onValueChange = { missing = it.take(500) },
                    label = { Text("What was missing? (optional)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Row(verticalAlignment = Alignment.CenterVertically) {
                    if (ui.saved) Text("Saved.", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f)) else Box(Modifier.weight(1f))
                    OutlinedButton(onClick = { rating?.let { onCheckin(it, missing) } }, enabled = !ui.busy) { Text("Save") }
                }
            }
        }
    }
}

/** A section folded to its title and count until tapped. */
@Composable
private fun Fold(title: String, count: String, content: @Composable () -> Unit) {
    var open by rememberSaveable(title) { mutableStateOf(false) }
    Surface(onClick = { open = !open }, shape = MaterialTheme.shapes.large, color = MaterialTheme.colorScheme.surfaceContainerLow, modifier = Modifier.fillMaxWidth().animateContentSize()) {
        Column(Modifier.padding(horizontal = 16.dp, vertical = 14.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(title, style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
                Text(count, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Icon(if (open) Icons.Rounded.ExpandLess else Icons.Rounded.ExpandMore, contentDescription = null, modifier = Modifier.padding(start = 8.dp))
            }
            if (open) content()
        }
    }
}

@Composable
private fun Said(text: String, receipt: Receipt, onLink: (String) -> Unit) {
    Column {
        Text(text, style = MaterialTheme.typography.bodyMedium)
        ReceiptLink(receipt, onLink)
    }
}

@Composable
private fun ReceiptLink(receipt: Receipt, onLink: (String) -> Unit) {
    val label = "${receipt.channel.ifEmpty { receipt.title }} ${duration(receipt.offsetS)}"
    val url = receipt.url
    if (url == null) Text(label, style = MaterialTheme.typography.labelMedium)
    else TextButton(onClick = { onLink(url) }, contentPadding = PaddingValues(0.dp)) { Text(label, style = MaterialTheme.typography.labelMedium) }
}

@Composable
private fun ChannelRow(channel: ChannelCard, busy: Boolean, onPause: () -> Unit) {
    fun share(value: Double?) = value?.let { "${Math.round(it * 100)}%" } ?: "–"
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Column(Modifier.weight(1f)) {
            Text(channel.title, style = MaterialTheme.typography.bodyLarge)
            Text(
                "${channel.videos} videos · ${share(channel.worthShare)} worth it · ${share(channel.engagedShare)} watched or liked" + if (channel.state == "paused") " · paused" else "",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (channel.suggestPause && channel.state != "paused") OutlinedButton(onClick = onPause, enabled = !busy) { Text("Pause") }
    }
}

@Composable
private fun ChangeRow(change: Change, busy: Boolean, onRevert: () -> Unit) {
    val text = change.after?.text ?: change.before?.text ?: "entry ${change.entryId}"
    val before = change.before?.weight
    val after = change.after?.weight
    val moved = if (before != null && after != null) "${signed(before)} → ${signed(after)}" else after?.let { signed(it) }.orEmpty()
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text("${change.op} $moved", style = MaterialTheme.typography.labelLargeEmphasized, color = MaterialTheme.colorScheme.primary)
            Text(text, style = MaterialTheme.typography.bodyLarge)
            change.reason?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
        }
        if (change.reverted) Text("reverted", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        else IconButton(onClick = onRevert, enabled = !busy) { Icon(Icons.Rounded.Undo, contentDescription = "Revert: ${change.op} $text") }
    }
}

/** "Sep 28": the Monday the week starts, from its key. */
private fun weekOf(key: String): String =
    runCatching { java.time.LocalDate.parse(key).format(java.time.format.DateTimeFormatter.ofPattern("MMM d", java.util.Locale.getDefault())) }.getOrDefault(key)
