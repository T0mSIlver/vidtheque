package dev.vidtheque.app.ui.feed

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.ExpandLess
import androidx.compose.material.icons.rounded.ExpandMore
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.Icon
import androidx.compose.material3.LargeFlexibleTopAppBar
import androidx.compose.material3.LoadingIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import coil3.compose.AsyncImage
import dev.vidtheque.app.data.FeedItem
import dev.vidtheque.app.ui.Matches
import dev.vidtheque.app.ui.ScoreDial
import dev.vidtheque.app.ui.dated
import dev.vidtheque.app.ui.duration
import dev.vidtheque.app.ui.scoreColor
import dev.vidtheque.app.ui.scoreWord
import dev.vidtheque.app.ui.thumbnail

/** How a still is drawn, so the caller can make it a shared element with the video screen. */
typealias Still = @Composable (videoId: String, modifier: Modifier) -> Unit

/** The modifier that makes a card one container with the page it opens (see Root); [corner] is this side's radius at rest. */
typealias Lift = @Composable (videoId: String, corner: Dp) -> Modifier

// Material's extra-large and large shapes, as numbers: the container transform
// interpolates the corner from the card's to the page's square one.
private val HERO_CORNER = 28.dp
private val ROW_CORNER = 16.dp

val plainStill: Still = { id, modifier ->
    AsyncImage(model = thumbnail(id), contentDescription = null, contentScale = ContentScale.Crop, modifier = modifier)
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun FeedScreen(
    ui: FeedUi,
    onRefresh: () -> Unit,
    onMore: () -> Unit,
    onToggleSkipped: () -> Unit,
    onMoreSkipped: () -> Unit,
    onOpen: (FeedItem) -> Unit,
    still: Still = plainStill,
    card: Lift = { _, _ -> Modifier },
    actions: @Composable () -> Unit = {},
) {
    val bar = TopAppBarDefaults.exitUntilCollapsedScrollBehavior()
    val list = rememberLazyListState()
    Scaffold(
        modifier = Modifier.nestedScroll(bar.nestedScrollConnection),
        containerColor = MaterialTheme.colorScheme.surface,
        topBar = {
            LargeFlexibleTopAppBar(
                title = { Text("Feed") },
                subtitle = if (ui.loaded) ({ Text(worthLine(ui)) }) else null,
                actions = { actions() },
                scrollBehavior = bar,
            )
        },
    ) { padding ->
        PullToRefreshBox(isRefreshing = ui.refreshing && ui.loaded, onRefresh = onRefresh, modifier = Modifier.padding(padding)) {
            if (!ui.loaded && ui.error == null) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { LoadingIndicator() }
                return@PullToRefreshBox
            }
            val nearEnd by remember { derivedStateOf { list.layoutInfo.visibleItemsInfo.lastOrNull()?.index?.let { it >= list.layoutInfo.totalItemsCount - 3 } == true } }
            LaunchedEffect(nearEnd) { if (nearEnd) onMore() }
            LazyColumn(
                state = list,
                contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 32.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.fillMaxSize(),
            ) {
                ui.error?.let { message -> item { Notice(message, "Try again", onRefresh) } }
                if (ui.loaded && ui.top.items.isEmpty()) {
                    item { Notice("Nothing to watch yet. New videos from the channels you follow land here once they are judged.") }
                }
                itemsIndexed(ui.top.items, key = { _, it -> "top-${it.videoId}" }) { index, item ->
                    if (index == 0) Hero(item, still, card) { onOpen(item) } else Row(item, still, card) { onOpen(item) }
                }
                if (ui.top.loading) item { Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) { LoadingIndicator() } }
                if (ui.skippedCount > 0 && ui.top.nextOffset == null) {
                    item(key = "skipped") { SkippedToggle(ui, onToggleSkipped) }
                    ui.skipped?.let { band ->
                        itemsIndexed(band.items, key = { _, it -> "skipped-${it.videoId}" }) { _, item -> Row(item, still, card) { onOpen(item) } }
                        if (band.nextOffset != null) item { TextButton(onClick = onMoreSkipped, enabled = !band.loading) { Text("More skipped") } }
                    }
                }
            }
        }
    }
}

private fun worthLine(ui: FeedUi): String =
    if (ui.top.items.isEmpty()) "Nothing new" else "Newest first"

@Composable
private fun Hero(item: FeedItem, still: Still, card: Lift, onClick: () -> Unit) {
    Card(onClick = onClick, shape = RoundedCornerShape(HERO_CORNER), modifier = Modifier.fillMaxWidth().then(card(item.videoId, HERO_CORNER))) {
        Box {
            still(item.videoId, Modifier.fillMaxWidth().aspectRatio(16f / 9f))
            Verdict(item.score, onImage = true, Modifier.align(Alignment.BottomStart).padding(12.dp))
        }
        Column(Modifier.padding(start = 16.dp, end = 16.dp, top = 14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(item.title, style = MaterialTheme.typography.titleLargeEmphasized, color = MaterialTheme.colorScheme.onSurface, maxLines = 3, overflow = TextOverflow.Ellipsis)
            Text(listOfNotNull(item.channel, item.publishedAt?.let { dated(it) }, duration(item.durationS)).joinToString(" · "), style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Reason(item, Modifier.padding(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 16.dp), lines = 3)
    }
}

@Composable
private fun Row(item: FeedItem, still: Still, card: Lift, onClick: () -> Unit) {
    Card(
        onClick = onClick,
        shape = RoundedCornerShape(ROW_CORNER),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainer),
        modifier = Modifier.fillMaxWidth().then(card(item.videoId, ROW_CORNER)),
    ) {
        Row(Modifier.padding(12.dp), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            still(item.videoId, Modifier.width(128.dp).aspectRatio(16f / 9f).clip(MaterialTheme.shapes.medium))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(listOfNotNull(item.channel, item.publishedAt?.let { dated(it) }).joinToString(" · "), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Text(item.title, style = MaterialTheme.typography.titleSmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Verdict(item.score, onImage = false, Modifier.weight(1f))
                    Text(duration(item.durationS), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
        Reason(item, Modifier.padding(start = 12.dp, end = 12.dp, bottom = 12.dp), lines = 2)
    }
}

@Composable
private fun Verdict(score: Int, onImage: Boolean, modifier: Modifier = Modifier) {
    Row(modifier, verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        if (onImage) {
            Surface(shape = MaterialTheme.shapes.extraLarge, color = MaterialTheme.colorScheme.surfaceContainerHigh.copy(alpha = 0.92f)) {
                Row(Modifier.padding(start = 4.dp, end = 12.dp, top = 4.dp, bottom = 4.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    ScoreDial(score, size = 36.dp)
                    Text(scoreWord(score), style = MaterialTheme.typography.labelLargeEmphasized, color = scoreColor(score))
                }
            }
        } else {
            ScoreDial(score, size = 32.dp)
            Text(scoreWord(score), style = MaterialTheme.typography.labelMediumEmphasized, color = scoreColor(score), maxLines = 1)
        }
    }
}

@Composable
private fun Reason(item: FeedItem, modifier: Modifier, lines: Int) {
    if (item.reason.isEmpty() && !item.explored && item.matches.isEmpty()) return
    Column(modifier, verticalArrangement = Arrangement.spacedBy(6.dp)) {
        if (item.matches.isNotEmpty()) Matches(item.matches, maxLines = lines - 1)
        if (item.reason.isNotEmpty()) Text(item.reason, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = lines, overflow = TextOverflow.Ellipsis)
        if (item.explored) {
            Surface(shape = MaterialTheme.shapes.small, color = MaterialTheme.colorScheme.tertiaryContainer) {
                Text("Outside your profile", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onTertiaryContainer, modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp))
            }
        }
    }
}

@Composable
private fun SkippedToggle(ui: FeedUi, onToggle: () -> Unit) {
    val count = if (ui.skippedCapped) "${ui.skippedCount}+" else "${ui.skippedCount}"
    Surface(onClick = onToggle, shape = MaterialTheme.shapes.extraLarge, color = MaterialTheme.colorScheme.surfaceContainerLow, modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.padding(horizontal = 20.dp, vertical = 16.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("Skipped ($count)", style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
            Icon(if (ui.skipped != null) Icons.Rounded.ExpandLess else Icons.Rounded.ExpandMore, contentDescription = null)
        }
    }
}

@Composable
private fun Notice(message: String, action: String? = null, onAction: () -> Unit = {}) {
    Surface(shape = MaterialTheme.shapes.large, color = MaterialTheme.colorScheme.surfaceContainer, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(message, style = MaterialTheme.typography.bodyLarge)
            if (action != null) TextButton(onClick = onAction) { Text(action) }
        }
    }
}
