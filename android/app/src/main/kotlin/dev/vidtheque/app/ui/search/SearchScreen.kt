package dev.vidtheque.app.ui.search

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.automirrored.rounded.OpenInNew
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.Search
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LoadingIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.vidtheque.app.data.SearchHit
import dev.vidtheque.app.ui.NO_APP
import dev.vidtheque.app.ui.dated
import dev.vidtheque.app.ui.duration
import dev.vidtheque.app.ui.feed.Lift
import dev.vidtheque.app.ui.openLink
import kotlinx.coroutines.launch

@Composable
fun SearchScreen(onBack: () -> Unit, onOpen: (SearchHit) -> Unit, card: Lift = { _, _ -> Modifier }) {
    val model: SearchViewModel = hiltViewModel()
    val ui by model.ui.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    SearchContent(
        ui = ui,
        snackbar = snackbar,
        card = card,
        onBack = onBack,
        onSubmit = model::submit,
        onMore = model::more,
        onRetry = model::retry,
        onOpen = onOpen,
        onMoment = { hit ->
            val link = receipt(hit.link) ?: return@SearchContent
            model.watched(hit)
            if (!context.openLink(link)) scope.launch { snackbar.showSnackbar(NO_APP) }
        },
    )
}

/** The search box under the bar, then one row per hit: the video, then the moment that matched. */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun SearchContent(
    ui: SearchUi,
    snackbar: SnackbarHostState = remember { SnackbarHostState() },
    card: Lift = { _, _ -> Modifier },
    onBack: () -> Unit = {},
    onSubmit: (String) -> Unit = {},
    onMore: () -> Unit = {},
    onRetry: () -> Unit = {},
    onOpen: (SearchHit) -> Unit = {},
    onMoment: (SearchHit) -> Unit = {},
) {
    var text by rememberSaveable { mutableStateOf(ui.query) }
    val keyboard = LocalSoftwareKeyboardController.current
    val focus = remember { FocusRequester() }
    // A fresh screen asks for a query; one coming back to its hits keeps the keyboard down.
    LaunchedEffect(Unit) { if (ui.query.isEmpty()) runCatching { focus.requestFocus() } }
    val list = rememberLazyListState()
    val nearEnd by remember { derivedStateOf { list.layoutInfo.visibleItemsInfo.lastOrNull()?.index?.let { it >= list.layoutInfo.totalItemsCount - 3 } == true } }
    LaunchedEffect(nearEnd, ui.hits.size) { if (nearEnd) onMore() }
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Search") },
                navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Rounded.ArrowBack, contentDescription = "Back") } },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        LazyColumn(Modifier.fillMaxSize().padding(padding), state = list, contentPadding = PaddingValues(start = 16.dp, end = 16.dp, bottom = 24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            item(key = "box") {
                OutlinedTextField(
                    value = text,
                    onValueChange = { text = it.take(QUERY_CHARS) },
                    modifier = Modifier.fillMaxWidth().focusRequester(focus),
                    placeholder = { Text("A phrase, a tool, a topic") },
                    leadingIcon = { Icon(Icons.Rounded.Search, contentDescription = null) },
                    trailingIcon = if (text.isEmpty()) null else ({ IconButton(onClick = { text = "" }) { Icon(Icons.Rounded.Close, contentDescription = "Clear") } }),
                    singleLine = true,
                    shape = MaterialTheme.shapes.extraLarge,
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                    keyboardActions = KeyboardActions(onSearch = {
                        keyboard?.hide()
                        onSubmit(text)
                    }),
                )
            }
            if (ui.query.isEmpty()) {
                item(key = "lead") {
                    Text("Every transcript, slide and frame in your corpus, searched the way your agent searches it.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            ui.notes.forEach { note ->
                item(key = "note:$note") { Text(note, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            }
            ui.empty?.let { empty -> item(key = "empty") { Text(empty, style = MaterialTheme.typography.bodyLarge) } }
            // The first hit of a video carries the container the video page grows from (Root, sharedCard).
            val firsts = ui.hits.withIndex().distinctBy { it.value.videoId }.map { it.index }.toSet()
            itemsIndexed(ui.hits, key = { index, hit -> "${hit.videoId}:${hit.matchStart ?: hit.start}:$index" }) { index, hit ->
                HitRow(hit, if (index in firsts) card(hit.videoId, 24.dp) else Modifier, onOpen, onMoment)
            }
            when {
                ui.error != null -> item(key = "error") {
                    Column {
                        Text(ui.error, style = MaterialTheme.typography.bodyLarge)
                        TextButton(onClick = onRetry) { Text("Try again") }
                    }
                }
                ui.loading -> item(key = "loading") { Box(Modifier.fillMaxWidth().padding(24.dp), contentAlignment = Alignment.Center) { LoadingIndicator() } }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
private fun HitRow(hit: SearchHit, container: Modifier, onOpen: (SearchHit) -> Unit, onMoment: (SearchHit) -> Unit) {
    Surface(onClick = { onOpen(hit) }, shape = MaterialTheme.shapes.extraLarge, color = MaterialTheme.colorScheme.surfaceContainerLow, modifier = container.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            val byline = listOfNotNull(hit.channel.ifEmpty { null }, hit.publishedAt?.let { dated(it) }).joinToString(" · ")
            if (byline.isNotEmpty()) Text(byline, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(hit.title.ifEmpty { hit.videoId }, style = MaterialTheme.typography.titleMediumEmphasized)
            val linked = receipt(hit.link) != null
            Surface(
                onClick = { onMoment(hit) },
                enabled = linked,
                shape = MaterialTheme.shapes.large,
                color = MaterialTheme.colorScheme.surfaceContainerHigh,
                modifier = Modifier.fillMaxWidth().padding(top = 4.dp),
            ) {
                Row(Modifier.padding(horizontal = 12.dp, vertical = 10.dp), verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    Surface(shape = MaterialTheme.shapes.medium, color = MaterialTheme.colorScheme.primaryContainer) {
                        Text(duration(hit.matchStart ?: hit.start), style = MaterialTheme.typography.labelLargeEmphasized, color = MaterialTheme.colorScheme.onPrimaryContainer, modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp))
                    }
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        if (hit.text != null) Text(hit.text, style = MaterialTheme.typography.bodyMedium, maxLines = 4)
                        else Text("Visual match, no text hit", style = MaterialTheme.typography.bodyMedium, fontStyle = FontStyle.Italic, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        val where = evidence(hit.source)
                        if (where.isNotEmpty()) Text(where, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    if (linked) Icon(Icons.AutoMirrored.Rounded.OpenInNew, contentDescription = "Open on YouTube at this moment", tint = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}

/** `source` in the demo's three words, in its order (web's `lib/schemas/evidence.ts`). */
fun evidence(source: String): String {
    val legs = source.split('+').toSet()
    return listOf("transcript" to "spoken", "ocr" to "on-screen", "frame" to "frame")
        .filter { it.first in legs }
        .joinToString(" · ") { it.second }
}

private const val QUERY_CHARS = 512
