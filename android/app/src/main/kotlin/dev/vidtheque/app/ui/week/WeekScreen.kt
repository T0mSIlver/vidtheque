package dev.vidtheque.app.ui.week

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.KeyboardArrowLeft
import androidx.compose.material.icons.automirrored.rounded.KeyboardArrowRight
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LargeFlexibleTopAppBar
import androidx.compose.material3.LoadingIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.unit.dp
import dev.vidtheque.app.data.FeedItem
import dev.vidtheque.app.data.Week
import dev.vidtheque.app.ui.feed.Hero
import dev.vidtheque.app.ui.feed.Lift
import dev.vidtheque.app.ui.feed.Row
import dev.vidtheque.app.ui.feed.Still
import dev.vidtheque.app.ui.feed.plainStill
import dev.vidtheque.app.ui.minutes
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.time.format.TextStyle
import java.util.Locale

/**
 * What should I watch this week? The week's ranked verdicts that fit the budget, then
 * a stop (companion.md §6). Every other video is one tap away, under "Show all videos",
 * and never mixed into this list.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun WeekScreen(
    ui: WeekUi,
    onRefresh: () -> Unit,
    onWeek: (String?) -> Unit,
    onBudget: (Int) -> Unit,
    onOpen: (FeedItem) -> Unit,
    onShowAll: () -> Unit,
    still: Still = plainStill,
    card: Lift = { _, _ -> Modifier },
    list: LazyListState = rememberLazyListState(),
    actions: @Composable () -> Unit = {},
) {
    val bar = TopAppBarDefaults.exitUntilCollapsedScrollBehavior()
    val week = ui.week
    val current = week != null && week.next == null
    Scaffold(
        modifier = Modifier.nestedScroll(bar.nestedScrollConnection),
        containerColor = MaterialTheme.colorScheme.surface,
        topBar = {
            LargeFlexibleTopAppBar(
                title = { Text(if (week == null || current) "This week" else "Week of ${day(week.week, "d MMM")}") },
                subtitle = week?.let { w -> { Text("${minutes(w.asksS)} of ${minutes(w.budgetMin * 60.0)}") } },
                actions = { actions() },
                scrollBehavior = bar,
            )
        },
    ) { padding ->
        PullToRefreshBox(isRefreshing = ui.refreshing && week != null, onRefresh = onRefresh, modifier = Modifier.padding(padding)) {
            if (week == null) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    if (ui.error != null) TextButton(onClick = onRefresh) { Text(ui.error) } else LoadingIndicator()
                }
                return@PullToRefreshBox
            }
            LazyColumn(
                state = list,
                contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 32.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
                modifier = Modifier.fillMaxSize(),
            ) {
                item(key = "head") { Head(week, ui.budgetFailed, onWeek, onBudget) }
                itemsIndexed(week.items, key = { _, it -> "week-${it.videoId}" }) { index, item ->
                    if (index == 0) Hero(item, still, card) { onOpen(item) } else Row(item, still, card) { onOpen(item) }
                }
                item(key = "end") { End(week, current, onShowAll) }
            }
        }
    }
}

/** Where [videoId]'s card sits in the week's list, as the LazyColumn lays it out, or null. */
fun weekIndex(ui: WeekUi, videoId: String): Int? =
    ui.week?.items?.indexOfFirst { it.videoId == videoId }?.takeIf { it >= 0 }?.let { it + 1 }

@Composable
private fun Head(week: Week, budgetFailed: Boolean, onWeek: (String?) -> Unit, onBudget: (Int) -> Unit) {
    var editing by remember { mutableStateOf(false) }
    val perDay = Math.round(week.budgetMin / 7.0).toInt()
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = { onWeek(week.previous) }) { Icon(Icons.AutoMirrored.Rounded.KeyboardArrowLeft, contentDescription = "The week before") }
            Text("${day(week.week, "d MMM")} – ${day(week.days.lastOrNull()?.day ?: week.week, "d MMM")}", style = MaterialTheme.typography.labelLarge, modifier = Modifier.weight(1f), color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (week.next != null) {
                IconButton(onClick = { onWeek(week.next) }) { Icon(Icons.AutoMirrored.Rounded.KeyboardArrowRight, contentDescription = "The week after") }
            }
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("$perDay min a day", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
            TextButton(onClick = { editing = true }) { Text("Change") }
        }
        if (budgetFailed) Text("The budget was not saved.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
        Days(week)
    }
    if (editing) BudgetDialog(perDay, onDismiss = { editing = false }) { editing = false; onBudget(it) }
}

/** What each day asks, a bar a day; the line is a day's share of the budget. Uploads come in bursts. */
@Composable
private fun Days(week: Week) {
    val daily = week.budgetMin * 60.0 / 7
    val most = maxOf(daily, week.days.maxOfOrNull { it.asksS } ?: 0.0, 1.0)
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
        week.days.forEach { d ->
            Column(
                horizontalAlignment = Alignment.CenterHorizontally,
                modifier = Modifier.weight(1f).clearAndSetSemantics {
                    contentDescription = "${day(d.day, "EEEE")}: ${minutes(d.asksS)}, ${d.fitted} of ${d.candidates} worth your time"
                },
            ) {
                Box(Modifier.fillMaxWidth().height(56.dp), contentAlignment = Alignment.BottomCenter) {
                    Box(Modifier.fillMaxWidth(0.6f).fillMaxHeight((d.asksS / most).toFloat()).background(MaterialTheme.colorScheme.primary, RoundedCornerShape(topStart = 4.dp, topEnd = 4.dp)))
                    Column(Modifier.fillMaxSize()) {
                        Spacer(Modifier.weight((1 - daily / most).toFloat().coerceAtLeast(0.001f)))
                        Box(Modifier.fillMaxWidth().height(1.dp).background(MaterialTheme.colorScheme.outline))
                        Spacer(Modifier.weight((daily / most).toFloat().coerceAtLeast(0.001f)))
                    }
                }
                Text(LocalDate.parse(d.day).dayOfWeek.getDisplayName(TextStyle.NARROW, Locale.getDefault()), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(if (d.asksS > 0) "${Math.round(d.asksS / 60)}" else " ", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@Composable
private fun BudgetDialog(perDay: Int, onDismiss: () -> Unit, onSave: (Int) -> Unit) {
    // Selected, so the first digit typed replaces the old figure instead of joining it.
    var field by remember { mutableStateOf(TextFieldValue(perDay.toString(), TextRange(0, perDay.toString().length))) }
    val value = field.text.toIntOrNull()?.takeIf { it in 0..1440 }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Your time") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("The feed fits a week of these. Uploads come in bursts, so a quiet day leaves time for a busy one.")
                OutlinedTextField(
                    value = field,
                    onValueChange = { field = it.copy(text = it.text.filter(Char::isDigit).take(4)) },
                    label = { Text("Minutes a day") },
                    singleLine = true,
                    isError = value == null,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                )
            }
        },
        confirmButton = { TextButton(onClick = { value?.let(onSave) }, enabled = value != null) { Text("Save") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}

/** The stop: the list ends here and says so, with every other video one tap away. */
@Composable
private fun End(week: Week, current: Boolean, onShowAll: () -> Unit) {
    Surface(shape = MaterialTheme.shapes.extraLarge, color = MaterialTheme.colorScheme.surfaceContainerHigh, modifier = Modifier.fillMaxWidth().padding(top = 8.dp)) {
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(
                when {
                    week.items.isEmpty() && current -> "Nothing worth your time yet this week."
                    week.items.isEmpty() -> "Nothing was worth your time that week."
                    current -> "That is everything worth your time this week."
                    else -> "That was everything worth your time that week."
                },
                style = MaterialTheme.typography.titleMediumEmphasized,
            )
            if (week.rest.count > 0) {
                val n = week.rest.count
                Text(
                    "$n more ${if (n == 1) "verdict asks" else "verdicts ask"} for ${minutes(week.rest.asksS)} past your budget.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            FilledTonalButton(onClick = onShowAll) { Text("Show all videos") }
        }
    }
}

private fun day(iso: String, pattern: String): String =
    LocalDate.parse(iso).format(DateTimeFormatter.ofPattern(pattern, Locale.getDefault()))
