package dev.vidtheque.app.ui.collection

import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LargeFlexibleTopAppBar
import androidx.compose.material3.LoadingIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.vidtheque.app.data.CollectionMoment
import dev.vidtheque.app.data.VideoRow
import dev.vidtheque.app.ui.NO_APP
import dev.vidtheque.app.ui.duration
import dev.vidtheque.app.ui.minutes
import dev.vidtheque.app.ui.openLink
import dev.vidtheque.app.ui.video.MomentRow
import dev.vidtheque.app.ui.video.repeatNote
import kotlinx.coroutines.launch

/**
 * The best moments on one interest, across videos, best first (companion.md §6.2).
 * Read from the top: a moment that says again what one above it said opens after
 * the repeat. A list that ends; nothing plays on its own (§8).
 */
@Composable
fun CollectionScreen(entryId: Long, text: String, onBack: () -> Unit, onOpen: (VideoRow) -> Unit) {
    val model = hiltViewModel<CollectionViewModel, CollectionViewModel.Factory>(key = "collection-$entryId", creationCallback = { it.create(entryId) })
    val ui by model.ui.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    CollectionContent(
        text = text,
        ui = ui,
        snackbar = snackbar,
        onBack = onBack,
        onRetry = model::load,
        onOpen = onOpen,
        onMoment = { moment ->
            if (context.openLink(Uri.parse(moment.url))) model.watched(moment)
            else scope.launch { snackbar.showSnackbar(NO_APP) }
        },
    )
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun CollectionContent(
    text: String,
    ui: CollectionUi,
    snackbar: SnackbarHostState,
    onBack: () -> Unit,
    onRetry: () -> Unit,
    onOpen: (VideoRow) -> Unit,
    onMoment: (CollectionMoment) -> Unit,
) {
    val bar = TopAppBarDefaults.exitUntilCollapsedScrollBehavior()
    val collection = ui.collection
    Scaffold(
        modifier = Modifier.nestedScroll(bar.nestedScrollConnection),
        topBar = {
            LargeFlexibleTopAppBar(
                title = { Text(collection?.entry?.text ?: text) },
                subtitle = collection?.let { c ->
                    { Text((if (c.moments.size == 1) "1 moment" else "${c.moments.size} moments") + ", " + minutes(c.momentsS)) }
                },
                navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Rounded.ArrowBack, contentDescription = "Back") } },
                scrollBehavior = bar,
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        if (collection == null) {
            Box(Modifier.fillMaxSize().padding(padding), contentAlignment = Alignment.Center) {
                if (ui.error != null) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Text(ui.error, style = MaterialTheme.typography.bodyLarge)
                        TextButton(onClick = onRetry) { Text("Try again") }
                    }
                } else LoadingIndicator()
            }
            return@Scaffold
        }
        LazyColumn(
            Modifier.fillMaxSize().padding(padding),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            itemsIndexed(collection.moments, key = { _, m -> "${m.video.videoId}:${m.offsetS}" }) { index, moment ->
                Column(verticalArrangement = Arrangement.spacedBy(4.dp), modifier = Modifier.padding(top = 8.dp)) {
                    TextButton(onClick = { onOpen(moment.video) }, contentPadding = PaddingValues(horizontal = 4.dp)) {
                        Text(
                            "${index + 1}. " + listOfNotNull(moment.video.channel, moment.video.title.ifEmpty { moment.video.videoId }).joinToString(" · "),
                            style = MaterialTheme.typography.labelLarge,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                    MomentRow(
                        span = duration(moment.startS) + "–" + duration(moment.endS),
                        why = moment.why,
                        note = moment.repeat?.let { repeatNote("moment ${it.item + 1}", it.whole, moment.startS - moment.offsetS) },
                        onClick = { onMoment(moment) },
                    )
                }
            }
            if (collection.moments.isEmpty()) {
                item { Text("No moment on this interest yet.", style = MaterialTheme.typography.bodyLarge) }
            }
            if (collection.hasMore) {
                item {
                    Text(
                        "More moments match this interest; these are the best ${collection.moments.size}.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = 12.dp),
                    )
                }
            }
        }
    }
}
