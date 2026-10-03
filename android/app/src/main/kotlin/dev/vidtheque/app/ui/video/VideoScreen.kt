package dev.vidtheque.app.ui.video

import android.content.ClipData
import android.content.ClipboardManager
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.automirrored.rounded.OpenInNew
import androidx.compose.material.icons.rounded.AutoAwesome
import androidx.compose.material.icons.rounded.Block
import androidx.compose.material.icons.rounded.ThumbDown
import androidx.compose.material.icons.rounded.ThumbUp
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.FilledIconToggleButton
import androidx.compose.material3.HorizontalFloatingToolbar
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LoadingIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.PlainTooltip
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.TooltipAnchorPosition
import androidx.compose.material3.TooltipBox
import androidx.compose.material3.TooltipDefaults
import androidx.compose.material3.rememberTooltipState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.unit.dp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.vidtheque.app.data.Moment
import dev.vidtheque.app.data.Verdict
import dev.vidtheque.app.data.claudeUri
import dev.vidtheque.app.data.videoPrompt
import dev.vidtheque.app.ui.Matches
import dev.vidtheque.app.ui.NO_APP
import dev.vidtheque.app.ui.ScoreDial
import dev.vidtheque.app.ui.VideoKey
import dev.vidtheque.app.ui.dated
import dev.vidtheque.app.ui.duration
import dev.vidtheque.app.ui.feed.Lift
import dev.vidtheque.app.ui.feed.Still
import dev.vidtheque.app.ui.openLink
import dev.vidtheque.app.ui.scoreColor
import dev.vidtheque.app.ui.scoreWord
import kotlinx.coroutines.launch

@Composable
fun VideoScreen(key: VideoKey, onBack: () -> Unit, still: Still, card: Lift = { _, _ -> Modifier }) {
    val model = hiltViewModel<VideoViewModel, VideoViewModel.Factory>(creationCallback = { it.create(key.videoId) })
    val ui by model.ui.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    // The undo window, on screen: Undo takes the signal back before it is sent.
    val waiting = ui.latest?.takeIf { ui.sent[it] == Sent.Waiting } ?: ui.sent.entries.lastOrNull { it.value == Sent.Waiting }?.key
    LaunchedEffect(waiting) {
        val kind = waiting ?: return@LaunchedEffect
        val said = SIGNALS.first { it.kind == kind }.done
        val result = snackbar.showSnackbar("$said. Noted for tonight's profile update.", actionLabel = "Undo", duration = SnackbarDuration.Indefinite)
        if (result == SnackbarResult.ActionPerformed) model.undo(kind)
    }
    VideoContent(
        key = key,
        ui = ui,
        still = still,
        container = card(key.videoId, MaterialTheme.shapes.extraLarge),
        snackbar = snackbar,
        onBack = onBack,
        onRetry = model::load,
        onSignal = { kind ->
            if (ui.sent[kind] == Sent.Done) scope.launch { snackbar.showSnackbar("Already sent. A sent signal can't be taken back.") }
            else model.toggle(kind)
        },
        onMoment = { moment ->
            model.watched(moment.offsetS)
            if (!context.openLink(Uri.parse(moment.url))) scope.launch { snackbar.showSnackbar(NO_APP) }
        },
        onAsk = { verdict ->
            // The Claude app opens claude.ai/new with `q` filled in (checked on Tom's phone,
            // 2026-10-03); the clipboard is only for a phone where nothing opens the link.
            val prompt = videoPrompt(verdict.video)
            model.askedClaude()
            if (!context.openLink(claudeUri(prompt))) {
                context.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("Ask Claude", prompt))
                scope.launch { snackbar.showSnackbar("Prompt copied. $NO_APP") }
            }
        },
    )
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun VideoContent(
    key: VideoKey,
    ui: VideoUi,
    still: Still,
    snackbar: SnackbarHostState,
    container: Modifier = Modifier,
    onBack: () -> Unit,
    onRetry: () -> Unit,
    onSignal: (String) -> Unit,
    onMoment: (Moment) -> Unit,
    onAsk: (Verdict) -> Unit,
) {
    val verdict = ui.verdict
    val title = verdict?.video?.title ?: key.title
    val channel = verdict?.video?.channel ?: key.channel
    Scaffold(
        modifier = container,
        topBar = {
            TopAppBar(
                title = {},
                navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Rounded.ArrowBack, contentDescription = "Back") } },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.surface),
            )
        },
        snackbarHost = { SnackbarHost(snackbar, Modifier.padding(bottom = 88.dp)) },
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding)) {
            Column(
                Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                still(key.videoId, Modifier.fillMaxWidth().aspectRatio(16f / 9f).clip(MaterialTheme.shapes.extraLarge))
                val byline = listOfNotNull(channel.ifEmpty { null }, verdict?.video?.publishedAt?.let { dated(it) }).joinToString(" · ")
                if (byline.isNotEmpty()) Text(byline, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (title.isNotEmpty()) Text(title, style = MaterialTheme.typography.headlineSmallEmphasized)
                when {
                    verdict != null -> Loaded(verdict, onMoment)
                    ui.error != null -> Column {
                        Text(ui.error, style = MaterialTheme.typography.bodyLarge)
                        TextButton(onClick = onRetry) { Text("Try again") }
                    }
                    else -> Box(Modifier.fillMaxWidth().padding(24.dp), contentAlignment = Alignment.Center) { LoadingIndicator() }
                }
                Spacer(Modifier.height(112.dp))
            }
            if (verdict != null) Actions(verdict, ui.sent, onSignal, onAsk, Modifier.align(Alignment.BottomCenter).navigationBarsPadding().padding(bottom = 16.dp))
        }
    }
}

@Composable
private fun Loaded(verdict: Verdict, onMoment: (Moment) -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        ScoreDial(verdict.score, size = 48.dp)
        Column {
            Text(scoreWord(verdict.score), style = MaterialTheme.typography.titleMediumEmphasized, color = scoreColor(verdict.score))
            Text(duration(verdict.video.durationS), style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
    if (verdict.explored) {
        Surface(shape = MaterialTheme.shapes.small, color = MaterialTheme.colorScheme.tertiaryContainer) {
            Text("Outside your profile", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onTertiaryContainer, modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp))
        }
    }
    if (verdict.matches.isNotEmpty()) Matches(verdict.matches)
    if (verdict.reason.isNotEmpty()) Text(verdict.reason, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    Text(verdict.summary, style = MaterialTheme.typography.bodyLarge)
    Text("Moments", style = MaterialTheme.typography.titleMediumEmphasized, modifier = Modifier.padding(top = 8.dp))
    verdict.moments.forEach { moment ->
        Surface(onClick = { onMoment(moment) }, shape = MaterialTheme.shapes.large, color = MaterialTheme.colorScheme.surfaceContainer, modifier = Modifier.fillMaxWidth()) {
            Row(Modifier.padding(horizontal = 14.dp, vertical = 12.dp), verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Surface(shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.primaryContainer) {
                    Text(duration(moment.offsetS), style = MaterialTheme.typography.labelLargeEmphasized, color = MaterialTheme.colorScheme.onPrimaryContainer, modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp))
                }
                Text(moment.why, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
                Icon(Icons.AutoMirrored.Rounded.OpenInNew, contentDescription = "Open on YouTube", tint = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
    val quiet = MaterialTheme.typography.bodyMedium
    if (verdict.moments.isEmpty() && verdict.momentsDropped == 0) {
        Text("This verdict names no moment; the summary is all it has.", style = quiet, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
    if (verdict.momentsDropped > 0) {
        val n = if (verdict.momentsDropped == 1) "1 moment is" else "${verdict.momentsDropped} moments are"
        Text("$n left out: a reindex removed the transcript line it cited, and the verdict will be rewritten.", style = quiet, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** One feedback button: its signal kind (companion.md §2.3), its label, and what the snackbar says once tapped. */
private class Signal(val kind: String, val icon: ImageVector, val label: String, val done: String)

// `mute` reads "Less like this": a bell said "notifications", which it never touched.
private val SIGNALS = listOf(
    Signal("thumb_up", Icons.Rounded.ThumbUp, "Liked it", "Liked"),
    Signal("thumb_down", Icons.Rounded.ThumbDown, "Didn't like it", "Disliked"),
    Signal("mute", Icons.Rounded.Block, "Less like this", "Less like this"),
)

/** Thumbs, "less like this" and Ask Claude, in reach of a thumb; a long press names each. */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun Actions(verdict: Verdict, sent: Map<String, Sent>, onSignal: (String) -> Unit, onAsk: (Verdict) -> Unit, modifier: Modifier) {
    val haptics = LocalHapticFeedback.current
    Column(modifier, horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (sent.values.any { it == Sent.Failed }) {
            Surface(shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.errorContainer) {
                Text("Not recorded. Tap again to retry.", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onErrorContainer, modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp))
            }
        }
        HorizontalFloatingToolbar(expanded = true) {
            SIGNALS.forEach { signal ->
                TooltipBox(
                    positionProvider = TooltipDefaults.rememberTooltipPositionProvider(TooltipAnchorPosition.Above),
                    tooltip = { PlainTooltip { Text(signal.label) } },
                    state = rememberTooltipState(),
                ) {
                    FilledIconToggleButton(
                        checked = sent[signal.kind].let { it == Sent.Waiting || it == Sent.Sending || it == Sent.Done },
                        onCheckedChange = {
                            haptics.performHapticFeedback(HapticFeedbackType.Confirm)
                            onSignal(signal.kind)
                        },
                        enabled = sent[signal.kind] != Sent.Sending,
                    ) { Icon(signal.icon, contentDescription = signal.label) }
                }
            }
            Spacer(Modifier.width(8.dp))
            Button(onClick = { onAsk(verdict) }) {
                Icon(Icons.Rounded.AutoAwesome, contentDescription = null, modifier = Modifier.padding(end = 8.dp))
                Text("Ask Claude")
            }
        }
    }
}
