package dev.vidtheque.app.ui.video

import android.content.ClipData
import android.content.ClipboardManager
import android.net.Uri
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.runtime.derivedStateOf
import dev.vidtheque.app.data.FeedItem
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.rounded.PlayArrow
import androidx.compose.material3.MaterialShapes
import androidx.compose.material3.toShape
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.activity.compose.LocalActivity
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat
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
import androidx.compose.material3.PlainTooltip
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.TooltipAnchorPosition
import androidx.compose.material3.TooltipBox
import androidx.compose.material3.TooltipDefaults
import androidx.compose.material3.rememberTooltipState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
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
import dev.vidtheque.app.data.SeenVideo
import dev.vidtheque.app.data.Verdict
import dev.vidtheque.app.data.claudeUri
import dev.vidtheque.app.data.videoPrompt
import dev.vidtheque.app.ui.Matches
import dev.vidtheque.app.ui.NO_APP
import dev.vidtheque.app.ui.ScoreDial
import dev.vidtheque.app.ui.VideoKey
import dev.vidtheque.app.ui.dated
import dev.vidtheque.app.ui.asked
import dev.vidtheque.app.ui.duration
import dev.vidtheque.app.ui.feed.Lift
import dev.vidtheque.app.ui.feed.Still
import dev.vidtheque.app.ui.openLink
import dev.vidtheque.app.ui.scoreColor
import dev.vidtheque.app.ui.scoreWord
import kotlinx.coroutines.launch

@Composable
fun VideoScreen(
    key: VideoKey,
    onBack: () -> Unit,
    still: Still,
    card: Lift = { _, _ -> Modifier },
    pages: List<FeedItem> = emptyList(),
    onMore: () -> Unit = {},
    onShown: (String) -> Unit = {},
    onOpenVideo: (VideoKey) -> Unit = {},
) {
    // The still runs under the status bar, behind a dark scrim: light icons in both modes.
    val window = LocalActivity.current?.window
    val view = LocalView.current
    DisposableEffect(window) {
        val bars = window?.let { WindowCompat.getInsetsController(it, view) }
        val was = bars?.isAppearanceLightStatusBars
        bars?.isAppearanceLightStatusBars = false
        onDispose { if (was != null) bars.isAppearanceLightStatusBars = was }
    }
    val start = pages.indexOfFirst { it.videoId == key.videoId }
    // Not in the feed's list (a notification opened it): this one video, no pager.
    if (start < 0) {
        VideoPage(key, settled = true, still = still, container = card(key.videoId, 0.dp), onBack = onBack, onOpenVideo = onOpenVideo)
        return
    }
    // The feed's order, swiped like Gmail's messages. A swipe only moves: no signal
    // but the `open` of the page it settles on. The container is the shown page's,
    // so back shrinks it into that video's card, which onShown scrolls into view.
    val pager = rememberPagerState(initialPage = start) { pages.size }
    val shown = pages.getOrNull(pager.settledPage)?.videoId ?: key.videoId
    LaunchedEffect(shown) { onShown(shown) }
    val nearEnd by remember { derivedStateOf { pager.currentPage >= pager.pageCount - 3 } }
    LaunchedEffect(nearEnd) { if (nearEnd) onMore() }
    HorizontalPager(pager, modifier = card(shown, 0.dp), key = { pages[it].videoId }) { page ->
        val item = pages[page]
        VideoPage(VideoKey(item.videoId, item.title, item.channel.orEmpty()), settled = pager.settledPage == page, still = still, onBack = onBack, onOpenVideo = onOpenVideo)
    }
}

@Composable
private fun VideoPage(key: VideoKey, settled: Boolean, still: Still, onBack: () -> Unit, onOpenVideo: (VideoKey) -> Unit, container: Modifier = Modifier) {
    val model = hiltViewModel<VideoViewModel, VideoViewModel.Factory>(key = "video-${key.videoId}", creationCallback = { it.create(key.videoId) })
    val ui by model.ui.collectAsStateWithLifecycle()
    LaunchedEffect(settled, ui.verdict != null || ui.unjudged != null) { if (settled) model.shown() }
    val context = LocalContext.current
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    // What a tap did, once the server has it; a second tap takes it back.
    var said by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(said, ui.saving) {
        val state = said ?: return@LaunchedEffect
        if (ui.saving) return@LaunchedEffect
        // Cleared only after the bar: clearing the key first would cancel this effect.
        if (!ui.failed) {
            val signal = SIGNALS.firstOrNull { it.state == state }
            snackbar.showSnackbar(signal?.let { "${it.done}. Tap it again to take it back." } ?: "Taken back.")
        }
        said = null
    }
    VideoContent(
        key = key,
        ui = ui,
        still = still,
        container = container,
        snackbar = snackbar,
        onBack = onBack,
        onRetry = model::load,
        onSignal = { state ->
            said = if (ui.feedback == state) "none" else state
            model.tap(state)
        },
        onPlay = {
            // From the start, like a moment at 0: the same link shape and the same signal.
            if (context.openLink(Uri.parse("https://youtu.be/${key.videoId}"))) model.watched(0.0)
            else scope.launch { snackbar.showSnackbar(NO_APP) }
        },
        onMoment = { moment ->
            if (context.openLink(Uri.parse(moment.url))) model.watched(moment.linkS)
            else scope.launch { snackbar.showSnackbar(NO_APP) }
        },
        onSeen = { seen -> onOpenVideo(VideoKey(seen.videoId, seen.title, seen.channel.orEmpty(), alone = true)) },
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
    onPlay: () -> Unit = {},
    onMoment: (Moment) -> Unit,
    onAsk: (Verdict) -> Unit,
    onSeen: (SeenVideo) -> Unit = {},
) {
    val verdict = ui.verdict
    val video = verdict?.video ?: ui.unjudged
    val title = video?.title ?: key.title
    val channel = video?.channel ?: key.channel
    // A Surface, not a background: it also sets the content colour the text reads.
    Surface(container.fillMaxSize(), color = MaterialTheme.colorScheme.surface) { Box {
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
            // Edge to edge at the very top, as the still sits at the top of a feed card:
            // the container transform then lands the one on the other (Root, sharedCard).
            Box(
                Modifier.clickable(onClickLabel = "Play from the start", role = Role.Button, onClick = onPlay)
                    .semantics { contentDescription = "Play on YouTube from the start" },
                contentAlignment = Alignment.Center,
            ) {
                still(key.videoId, Modifier.fillMaxWidth().aspectRatio(16f / 9f))
                // So the status bar and the back button read over any still.
                Box(Modifier.matchParentSize().background(Brush.verticalGradient(0f to Color.Black.copy(alpha = 0.55f), 0.4f to Color.Transparent)))
                // Play, in one of Expressive's shapes: the still opens the video from the start.
                Surface(shape = MaterialShapes.Cookie9Sided.toShape(), color = MaterialTheme.colorScheme.primaryContainer, contentColor = MaterialTheme.colorScheme.onPrimaryContainer, modifier = Modifier.size(72.dp)) {
                    Box(contentAlignment = Alignment.Center) { Icon(Icons.Rounded.PlayArrow, contentDescription = null, modifier = Modifier.size(40.dp)) }
                }
            }
            Column(Modifier.padding(start = 16.dp, end = 16.dp, top = 16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                val byline = listOfNotNull(channel.ifEmpty { null }, video?.publishedAt?.let { dated(it) }).joinToString(" · ")
                if (byline.isNotEmpty()) Text(byline, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (title.isNotEmpty()) Text(title, style = MaterialTheme.typography.headlineSmallEmphasized)
                when {
                    verdict != null -> Loaded(verdict, onMoment, onSeen)
                    ui.unjudged != null -> Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(duration(ui.unjudged.durationS), style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text("No verdict yet: this video has not been scored against your profile.", style = MaterialTheme.typography.bodyLarge)
                    }
                    ui.error != null -> Column {
                        Text(ui.error, style = MaterialTheme.typography.bodyLarge)
                        TextButton(onClick = onRetry) { Text("Try again") }
                    }
                    else -> Box(Modifier.fillMaxWidth().padding(24.dp), contentAlignment = Alignment.Center) { LoadingIndicator() }
                }
                Spacer(Modifier.height(112.dp))
            }
        }
        FilledIconButton(
            onClick = onBack,
            colors = IconButtonDefaults.filledIconButtonColors(containerColor = Color.Black.copy(alpha = 0.45f), contentColor = Color.White),
            modifier = Modifier.statusBarsPadding().padding(8.dp),
        ) { Icon(Icons.AutoMirrored.Rounded.ArrowBack, contentDescription = "Back") }
        if (verdict != null) Actions(verdict, ui.feedback, ui.saving, ui.failed, onSignal, onAsk, Modifier.align(Alignment.BottomCenter).navigationBarsPadding().padding(bottom = 16.dp))
        SnackbarHost(snackbar, Modifier.align(Alignment.BottomCenter).navigationBarsPadding().padding(bottom = 88.dp))
    } }
}

@Composable
private fun Loaded(verdict: Verdict, onMoment: (Moment) -> Unit, onSeen: (SeenVideo) -> Unit) {
    val shown = verdict.tier ?: verdict.score
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
        ScoreDial(shown, size = 48.dp)
        Column {
            Text(scoreWord(shown), style = MaterialTheme.typography.titleMediumEmphasized, color = scoreColor(shown))
            Text(asked(verdict.momentsS, verdict.video.durationS), style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
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
        MomentRow(
            span = duration(moment.linkS) + (moment.endS?.let { "–" + duration(it) } ?: ""),
            why = moment.why,
            note = moment.repeat?.let { repeatNote("“${it.title}”", it.whole, moment.linkS - moment.offsetS) },
            onClick = { onMoment(moment) },
        )
    }
    val quiet = MaterialTheme.typography.bodyMedium
    if (verdict.moments.isEmpty() && verdict.momentsDropped == 0) {
        Text("This verdict names no moment; the summary is all it has.", style = quiet, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
    if (verdict.momentsDropped > 0) {
        val n = if (verdict.momentsDropped == 1) "1 moment is" else "${verdict.momentsDropped} moments are"
        Text("$n left out: a reindex removed the transcript line it cited, and the verdict will be rewritten.", style = quiet, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
    // The stretches said before in videos you saw (companion.md §3.2); each opens that video here.
    if (verdict.overlaps.isNotEmpty()) {
        Text("Seen before", style = MaterialTheme.typography.titleMediumEmphasized, modifier = Modifier.padding(top = 8.dp))
        verdict.overlaps.forEach { overlap ->
            Surface(onClick = { onSeen(overlap.video) }, shape = MaterialTheme.shapes.large, color = MaterialTheme.colorScheme.surfaceContainerLow, modifier = Modifier.fillMaxWidth()) {
                Row(Modifier.padding(horizontal = 14.dp, vertical = 12.dp), verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text(duration(overlap.startS) + "–" + duration(overlap.endS), style = MaterialTheme.typography.labelLargeEmphasized, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(vertical = 2.dp))
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text(overlap.video.title.ifEmpty { overlap.video.videoId }, style = MaterialTheme.typography.bodyMedium)
                        Text(
                            listOfNotNull(overlap.video.channel, "said there from ${duration(overlap.seenS)}").joinToString(" · "),
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }
    }
}

/** A moment as a link out: its span, why, and what it repeats, when it does. */
@Composable
fun MomentRow(span: String, why: String, note: String?, onClick: () -> Unit) {
    Surface(onClick = onClick, shape = MaterialTheme.shapes.large, color = MaterialTheme.colorScheme.surfaceContainer, modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.padding(horizontal = 14.dp, vertical = 12.dp), verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Surface(shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.primaryContainer) {
                Text(span, style = MaterialTheme.typography.labelLargeEmphasized, color = MaterialTheme.colorScheme.onPrimaryContainer, modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp))
            }
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(why, style = MaterialTheme.typography.bodyMedium)
                note?.let { Text(it, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            }
            Icon(Icons.AutoMirrored.Rounded.OpenInNew, contentDescription = "Open on YouTube", tint = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** What a moment repeats, and what its link skips (companion.md §3.2). [where] is the
 *  video you saw it in, or, on a collection, the moment above it. */
fun repeatNote(where: String, whole: Boolean, skippedS: Double): String = when {
    whole -> "You saw all of it in $where"
    skippedS > 0 -> "Skips ${duration(skippedS)} you saw in $where"
    else -> "You saw part of it in $where"
}

/** One feedback button: the state it sets (companion.md §2.3), its label, and what the snackbar says once set. */
private class Signal(val state: String, val icon: ImageVector, val label: String, val done: String)

// `mute` reads "Less like this": a bell said "notifications", which it never touched.
private val SIGNALS = listOf(
    Signal("up", Icons.Rounded.ThumbUp, "Liked it", "Liked"),
    Signal("down", Icons.Rounded.ThumbDown, "Didn't like it", "Disliked"),
    Signal("muted", Icons.Rounded.Block, "Less like this", "Less like this"),
)

/** Thumbs, "less like this" and Ask Claude, in reach of a thumb; a long press names each, and the one set shows filled. */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun Actions(verdict: Verdict, feedback: String, saving: Boolean, failed: Boolean, onSignal: (String) -> Unit, onAsk: (Verdict) -> Unit, modifier: Modifier) {
    val haptics = LocalHapticFeedback.current
    Column(modifier, horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        if (failed) {
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
                        checked = feedback == signal.state,
                        onCheckedChange = {
                            haptics.performHapticFeedback(HapticFeedbackType.Confirm)
                            onSignal(signal.state)
                        },
                        enabled = !saving,
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
