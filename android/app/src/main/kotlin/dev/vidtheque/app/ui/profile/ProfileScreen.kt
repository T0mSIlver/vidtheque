package dev.vidtheque.app.ui.profile

import android.Manifest
import android.content.ClipData
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import android.content.ClipboardManager
import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.rounded.ExpandLess
import androidx.compose.material.icons.rounded.ExpandMore
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.sp
import dev.vidtheque.app.ui.dated
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.selection.toggleable
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
import androidx.compose.material3.Switch
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
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.vidtheque.app.data.CostWindow
import dev.vidtheque.app.data.ValuedTime
import dev.vidtheque.app.data.PROFILE_PROMPT
import dev.vidtheque.app.data.ProfileEntry
import dev.vidtheque.app.data.ProfileEvent
import dev.vidtheque.app.data.claudeUri
import dev.vidtheque.app.data.signed
import dev.vidtheque.app.ui.NO_APP
import dev.vidtheque.app.ui.WeightDial
import dev.vidtheque.app.ui.openLink
import kotlinx.coroutines.launch

@Composable
fun ProfileScreen(onBack: () -> Unit, onSignOut: () -> Unit) {
    val model: ProfileViewModel = hiltViewModel()
    val ui by model.ui.collectAsStateWithLifecycle()
    val pushOn by model.pushOn.collectAsStateWithLifecycle()
    val costs by hiltViewModel<CostViewModel>().month.collectAsStateWithLifecycle()
    val ledger by hiltViewModel<ValuedTimeViewModel>().ledger.collectAsStateWithLifecycle()
    // Asked when the reader turns notifications on, never at launch.
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) model.setPush(true)
    }
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
        push = if (model.pushAvailable) pushOn else null,
        costs = costs,
        ledger = ledger,
        onPush = { on ->
            if (!on) model.setPush(false)
            else if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                permission.launch(Manifest.permission.POST_NOTIFICATIONS)
            } else model.setPush(true)
        },
        onBuild = {
            // As on the video screen: the Claude app keeps `q`; copy only when nothing opens the link.
            if (!context.openLink(claudeUri(PROFILE_PROMPT))) {
                context.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("Profile interview", PROFILE_PROMPT))
                scope.launch { snackbar.showSnackbar("Prompt copied. $NO_APP") }
            }
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
    push: Boolean? = null,
    onPush: (Boolean) -> Unit = {},
    costs: CostWindow? = null,
    ledger: ValuedTime? = null,
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
                    Text("Ask Claude to interview me", style = ButtonDefaults.textStyleFor(ButtonDefaults.MediumContainerHeight))
                }
            }
            item {
                Text(
                    "Five questions, once in a while. Claude saves the answers with the profile tool when vidtheque is one of its connectors.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                )
            }
            if (push != null) item { Notifications(push, enabled = !ui.busy, onPush) }
            ledger?.let { item { ValuedTimeCard(it) } }
            if (costs != null && costs.calls > 0) item { CostCard(costs) }
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

// An entry's text is a title over 12/16 notes; at the scale's 16/24 its lines sat
// apart from everything under them, so it keeps the notes' ratio (1.3).
@Composable
private fun entryStyle() = MaterialTheme.typography.titleMedium.copy(lineHeight = 21.sp)

/** One interest: its text whole, and the note that put it there, folded to two lines until tapped. */
@Composable
private fun Entry(entry: ProfileEntry, busy: Boolean, onDrop: () -> Unit) {
    var open by rememberSaveable(entry.id) { mutableStateOf(false) }
    Surface(
        onClick = { open = !open },
        shape = MaterialTheme.shapes.large,
        color = MaterialTheme.colorScheme.surfaceContainer,
        modifier = Modifier.fillMaxWidth().animateContentSize(),
    ) {
        Row(Modifier.padding(start = 12.dp, top = 12.dp, bottom = 12.dp, end = 4.dp), verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            WeightDial(entry.weight, size = 48.dp)
            Column(Modifier.weight(1f).padding(top = 2.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(entry.text, style = entryStyle())
                Text(
                    listOfNotNull("by ${source(entry.source)}", entry.createdAt?.let { dated(it) }, entry.expiresAt?.let { lapses(it) }).joinToString(" · "),
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                entry.evidence?.let { Note(it, open) }
            }
            IconButton(onClick = onDrop, enabled = !busy) { Icon(Icons.Rounded.Close, contentDescription = "Drop ${entry.text}") }
        }
    }
}

/** A reason or evidence: two lines with a chevron while folded, all of it once open. */
@Composable
private fun Note(text: String, open: Boolean) {
    var cut by remember(text) { mutableStateOf(false) }
    Row(verticalAlignment = Alignment.Bottom) {
        Text(
            text,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = if (open) Int.MAX_VALUE else 2,
            overflow = TextOverflow.Ellipsis,
            onTextLayout = { if (!open) cut = it.hasVisualOverflow },
            modifier = Modifier.weight(1f),
        )
        if (cut || open) Icon(
            if (open) Icons.Rounded.ExpandLess else Icons.Rounded.ExpandMore,
            contentDescription = if (open) "Show less" else "Show more",
            tint = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.size(20.dp),
        )
    }
}

/**
 * One change per row, every row the same columns: the weight it moved (old, struck,
 * over new), then what happened to which entry, who did it and when, then why.
 */
@Composable
private fun HistoryRow(event: ProfileEvent, names: Map<Long, String>, busy: Boolean, onRevert: () -> Unit) {
    val text = event.after?.text ?: event.before?.text ?: names[event.entryId] ?: "entry ${event.entryId}"
    var open by rememberSaveable(event.id) { mutableStateOf(false) }
    Surface(
        onClick = { open = !open },
        shape = MaterialTheme.shapes.large,
        color = MaterialTheme.colorScheme.surfaceContainerLow,
        modifier = Modifier.fillMaxWidth().animateContentSize(),
    ) {
        Row(Modifier.padding(start = 12.dp, top = 12.dp, bottom = 12.dp, end = 4.dp), verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Moved(event, Modifier.width(56.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(what(event), style = MaterialTheme.typography.labelLargeEmphasized, color = MaterialTheme.colorScheme.primary)
                Text(text, style = entryStyle().copy(fontSize = MaterialTheme.typography.titleSmall.fontSize, lineHeight = MaterialTheme.typography.titleSmall.lineHeight))
                Text(
                    "by ${source(event.actor)} · ${dated(event.at)}",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                event.reason?.takeIf { it.isNotBlank() }?.let { Box(Modifier.padding(top = 2.dp)) { Note(it, open) } }
            }
            IconButton(onClick = onRevert, enabled = !busy) { Icon(Icons.Rounded.Undo, contentDescription = "Revert: ${what(event)} $text") }
        }
    }
}

/** The weight column: the new weight large in its sign's colour, the old one struck above it. */
@Composable
private fun Moved(event: ProfileEvent, modifier: Modifier) {
    val old = event.before?.weight
    val new = event.after?.weight
    val gone = event.before?.live == true && event.after?.live == false
    Column(modifier, horizontalAlignment = Alignment.End) {
        if (old != null && (new != null && new != old || gone)) {
            Text(
                signed(old),
                style = MaterialTheme.typography.labelMedium.copy(textDecoration = if (gone) null else TextDecoration.LineThrough),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        when {
            gone -> Text("off", style = MaterialTheme.typography.titleMediumEmphasized, color = MaterialTheme.colorScheme.onSurfaceVariant)
            new != null -> Text(signed(new), style = MaterialTheme.typography.titleMediumEmphasized, color = if (new < 0) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary)
            old != null -> Text(signed(old), style = MaterialTheme.typography.titleMediumEmphasized, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** Who wrote it, in the reader's words: the actors are the server's (companion.md §2.1). */
/** "project, 12 days left": a project lapses unless written again (#159). */
private fun lapses(expiresAt: Long, now: Long = System.currentTimeMillis() / 1000): String {
    val days = maxOf(0L, (expiresAt - now + 86_399) / 86_400)
    return "project, $days ${if (days == 1L) "day" else "days"} left"
}

private fun source(actor: String): String = when (actor) {
    "owner" -> "you"
    "app" -> "you, in the app"
    "agent" -> "an agent"
    "nightly" -> "the nightly update"
    else -> actor
}

/** What the event did, from its op and the states on either side. */
private fun what(event: ProfileEvent): String {
    val before = event.before
    val after = event.after
    val change = when {
        before == null -> "Added"
        before.live == true && after?.live == false -> "Dropped"
        before.live == false && after?.live == true -> "Brought back"
        before.weight != null && after?.weight != null && before.weight != after.weight -> "Reweighted"
        before.text != null && after?.text != null && before.text != after.text -> "Reworded"
        else -> event.op.replaceFirstChar { it.uppercase() }
    }
    return if (event.op == "revert") "$change (revert)" else change
}

/** This phone, yes or no; which verdicts count is the instance's threshold (companion.md §6). */
@Composable
private fun Notifications(on: Boolean, enabled: Boolean, onChange: (Boolean) -> Unit) {
    Surface(shape = MaterialTheme.shapes.large, color = MaterialTheme.colorScheme.surfaceContainerHigh, modifier = Modifier.fillMaxWidth()) {
        Row(
            Modifier.toggleable(value = on, enabled = enabled, role = Role.Switch, onValueChange = onChange).padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Column(Modifier.weight(1f)) {
                Text("Notify this phone", style = MaterialTheme.typography.titleMedium)
                Text("When a new video is judged worth watching whole", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Switch(checked = on, onCheckedChange = null, enabled = enabled)
        }
    }
}

