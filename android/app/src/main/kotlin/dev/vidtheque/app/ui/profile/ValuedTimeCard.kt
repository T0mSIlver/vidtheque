package dev.vidtheque.app.ui.profile

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dev.vidtheque.app.data.Api
import dev.vidtheque.app.data.ValuedTime
import dev.vidtheque.app.data.Week
import javax.inject.Inject
import kotlin.math.roundToInt
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/** The weekly ledger, read once per visit; a failed read shows no card rather than an error. */
@HiltViewModel
class ValuedTimeViewModel @Inject constructor(private val api: Api) : ViewModel() {
    private val _ledger = MutableStateFlow<ValuedTime?>(null)
    val ledger: StateFlow<ValuedTime?> = _ledger.asStateFlow()

    init {
        viewModelScope.launch {
            _ledger.value = try {
                api.valuedTime()
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                null
            }
        }
    }
}

/** This week's hit rate, regret and misses, and last week's beside them (companion.md §3.3). */
@Composable
fun ValuedTimeCard(ledger: ValuedTime) {
    val week = ledger.weeks.firstOrNull() ?: return
    Surface(shape = MaterialTheme.shapes.large, color = MaterialTheme.colorScheme.surfaceContainerHigh, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(horizontal = 16.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("This week against YouTube", style = MaterialTheme.typography.titleMedium)
            Row(horizontalArrangement = Arrangement.spacedBy(28.dp)) {
                Figure(percent(week.hits.rate), "hit rate")
                Figure(percent(week.regret.rate), "regret", over = (week.regret.rate ?: 0.0) > ledger.regretTarget)
                Figure("${week.misses.count}", if (week.misses.count == 1) "miss" else "misses")
            }
            Text(detail(week, ledger.regretTarget), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            week.picks?.takeIf { it.picked > 0 }?.let { picks ->
                Text(
                    "Claude's picks: kept ${picks.kept} of ${picks.picked}, ${percent(picks.rate)} · top tier ${percent(week.top?.rate)}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            ledger.weeks.getOrNull(1)?.let {
                Text(
                    "Last week: ${percent(it.hits.rate)} hits · ${percent(it.regret.rate)} regret · ${it.misses.count} missed",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun Figure(value: String, label: String, over: Boolean = false) {
    Column {
        Text(
            value,
            style = MaterialTheme.typography.headlineSmallEmphasized,
            color = if (over) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary,
        )
        Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

fun percent(rate: Double?): String = rate?.let { "${(it * 100).roundToInt()}%" } ?: "—"

fun detail(week: Week, target: Double): String {
    val pending = if (week.misses.pending > 0) " · ${week.misses.pending} shared, not judged yet" else ""
    val outside = week.outside?.takeIf { it.shown > 0 }?.let { " · kept ${it.kept} of ${it.shown} from outside" } ?: ""
    return "Kept ${week.hits.kept} of ${week.hits.offered} scored 2–3 · regretted ${week.regret.down} of ${week.regret.watched} watched, target under ${percent(target)}$pending$outside"
}
