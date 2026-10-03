package dev.vidtheque.app.ui.profile

import android.content.ClipData
import android.content.ClipboardManager
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.automirrored.rounded.Logout
import androidx.compose.material.icons.rounded.AutoAwesome
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.Undo
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LargeFlexibleTopAppBar
import androidx.compose.material3.LoadingIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.vidtheque.app.data.PROFILE_PROMPT
import dev.vidtheque.app.data.ProfileEntry
import dev.vidtheque.app.data.ProfileEvent
import dev.vidtheque.app.data.claudeUri
import dev.vidtheque.app.data.signed
import dev.vidtheque.app.ui.NO_APP
import dev.vidtheque.app.ui.WeightDial
import dev.vidtheque.app.ui.openLink
import kotlinx.coroutines.launch
import java.text.DateFormat
import java.util.Date

@Composable
fun ProfileScreen(onBack: () -> Unit, onSignOut: () -> Unit) {
    val model: ProfileViewModel = hiltViewModel()
    val ui by model.ui.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    ProfileContent(
        ui = ui,
        snackbar = snackbar,
        onBack = onBack,
        onSignOut = onSignOut,
        onRetry = model::load,
        onDrop = model::drop,
        onRevert = model::revert,
        onOlder = model::older,
        onBuild = {
            context.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("Build my profile", PROFILE_PROMPT))
            val opened = context.openLink(claudeUri(PROFILE_PROMPT))
            scope.launch { snackbar.showSnackbar(if (opened) "Prompt copied. Paste it if Claude opens empty." else "Prompt copied. $NO_APP") }
        },
    )
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun ProfileContent(
    ui: ProfileUi,
    snackbar: SnackbarHostState,
    onBack: () -> Unit,
    onSignOut: () -> Unit,
    onRetry: () -> Unit,
    onDrop: (Long) -> Unit,
    onRevert: (Long) -> Unit,
    onOlder: () -> Unit,
    onBuild: () -> Unit,
) {
    val bar = TopAppBarDefaults.exitUntilCollapsedScrollBehavior()
    val profile = ui.profile
    Scaffold(
        modifier = Modifier.nestedScroll(bar.nestedScrollConnection),
        topBar = {
            LargeFlexibleTopAppBar(
                title = { Text("Your interests") },
                subtitle = profile?.let { { Text("${it.entries.size} of ${it.maxEntries} · they score every new verdict") } },
                navigationIcon = { IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Rounded.ArrowBack, contentDescription = "Back") } },
                actions = { IconButton(onClick = onSignOut) { Icon(Icons.AutoMirrored.Rounded.Logout, contentDescription = "Sign out") } },
                scrollBehavior = bar,
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        if (profile == null) {
            Column(Modifier.fillMaxSize().padding(padding).padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
                if (ui.error != null) {
                    Text(ui.error, style = MaterialTheme.typography.bodyLarge)
                    TextButton(onClick = onRetry) { Text("Try again") }
                } else {
                    LoadingIndicator()
                }
            }
            return@Scaffold
        }
        val names = remember(profile) { profile.entries.associate { it.id to it.text } }
        LazyColumn(
            Modifier.fillMaxSize().padding(padding),
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            ui.error?.let { item { Text(it, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.error) } }
            item {
                Button(
                    onClick = onBuild,
                    modifier = Modifier.fillMaxWidth().height(ButtonDefaults.MediumContainerHeight),
                    contentPadding = ButtonDefaults.contentPaddingFor(ButtonDefaults.MediumContainerHeight),
                ) {
                    Icon(Icons.Rounded.AutoAwesome, contentDescription = null, modifier = Modifier.padding(end = 8.dp))
                    Text("Ask Claude to build my profile", style = ButtonDefaults.textStyleFor(ButtonDefaults.MediumContainerHeight))
                }
            }
            item {
                Text(
                    "Claude saves the list with the profile tool when vidtheque is one of its connectors.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                )
            }
            if (profile.entries.isEmpty()) {
                item { Text("No interests yet, so every verdict is scored without them.", style = MaterialTheme.typography.bodyLarge) }
            }
            items(profile.entries.sortedByDescending { it.weight }, key = { "entry-${it.id}" }) { entry ->
                Entry(entry, busy = ui.busy) { onDrop(entry.id) }
            }
            item { Text("History", style = MaterialTheme.typography.titleMediumEmphasized, modifier = Modifier.padding(top = 16.dp, start = 4.dp)) }
            if (ui.events.isEmpty()) item { Text("No change yet.", style = MaterialTheme.typography.bodyMedium) }
            items(ui.events, key = { "event-${it.id}" }) { event ->
                HistoryRow(event, names, busy = ui.busy) { onRevert(event.id) }
            }
            if (ui.nextBefore != null) item { OutlinedButton(onClick = onOlder, enabled = !ui.busy) { Text("Older") } }
        }
    }
}

@Composable
private fun Entry(entry: ProfileEntry, busy: Boolean, onDrop: () -> Unit) {
    Surface(shape = MaterialTheme.shapes.large, color = MaterialTheme.colorScheme.surfaceContainer, modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.padding(start = 12.dp, top = 10.dp, bottom = 10.dp, end = 4.dp), verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            WeightDial(entry.weight)
            Column(Modifier.weight(1f)) {
                Text(entry.text, style = MaterialTheme.typography.bodyLarge)
                Text(
                    listOfNotNull(source(entry.source), entry.evidence).joinToString(" · "),
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
            }
            IconButton(onClick = onDrop, enabled = !busy) { Icon(Icons.Rounded.Close, contentDescription = "Drop ${entry.text}") }
        }
    }
}

@Composable
private fun HistoryRow(event: ProfileEvent, names: Map<Long, String>, busy: Boolean, onRevert: () -> Unit) {
    val text = event.after?.text ?: event.before?.text ?: names[event.entryId] ?: "entry ${event.entryId}"
    Surface(shape = MaterialTheme.shapes.large, color = MaterialTheme.colorScheme.surfaceContainerLow, modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.padding(start = 16.dp, top = 10.dp, bottom = 10.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(listOf(event.op, change(event)).filter { it.isNotEmpty() }.joinToString(" · "), style = MaterialTheme.typography.labelLargeEmphasized, color = MaterialTheme.colorScheme.primary)
                Text(text, style = MaterialTheme.typography.bodyMedium)
                Text(
                    listOfNotNull(source(event.actor), DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT).format(Date(event.at * 1000)), event.reason).joinToString(" · "),
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            IconButton(onClick = onRevert, enabled = !busy) { Icon(Icons.Rounded.Undo, contentDescription = "Revert: ${event.op} $text") }
        }
    }
}

/** Who wrote it, in the reader's words: the actors are the server's (companion.md §2.1). */
private fun source(actor: String): String = when (actor) {
    "owner" -> "you"
    "app" -> "you, in the app"
    "agent" -> "an agent"
    "nightly" -> "the nightly update"
    else -> actor
}

/** What the event did to the entry, in weights and liveness, as the web prints it. */
private fun change(event: ProfileEvent): String {
    val before = event.before
    val after = event.after
    val parts = mutableListOf<String>()
    if (before == null && after?.weight != null) parts += signed(after.weight)
    if (before?.weight != null && after?.weight != null && before.weight != after.weight) parts += "${signed(before.weight)} → ${signed(after.weight)}"
    if (before?.live == true && after?.live == false) parts += "retired"
    if (before?.live == false && after?.live == true) parts += "back"
    return parts.joinToString(" · ")
}
