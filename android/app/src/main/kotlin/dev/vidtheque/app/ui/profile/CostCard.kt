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
import dev.vidtheque.app.data.CostWindow
import dev.vidtheque.app.data.usd
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

/** This month's model cost, read once per visit; a failed read shows no card rather than an error. */
@HiltViewModel
class CostViewModel @Inject constructor(private val api: Api) : ViewModel() {
    private val _month = MutableStateFlow<CostWindow?>(null)
    val month: StateFlow<CostWindow?> = _month.asStateFlow()

    init {
        viewModelScope.launch {
            _month.value = try {
                api.costs().windows.month
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                null
            }
        }
    }
}

/** List price: on a monthly plan, what the calls would cost pay-as-you-go (companion.md §4.1). */
@Composable
fun CostCard(month: CostWindow) {
    Surface(shape = MaterialTheme.shapes.large, color = MaterialTheme.colorScheme.surfaceContainerHigh, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(horizontal = 16.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Model cost", style = MaterialTheme.typography.titleMedium)
            Row(horizontalArrangement = Arrangement.spacedBy(32.dp)) {
                Figure(usd(month.costMicroUsd), "this month")
                Figure(usd(month.perVerdictMicroUsd), "per verdict")
            }
            val unpriced = if (month.unpricedCalls > 0) " · ${month.unpricedCalls} with no known cost" else ""
            Text(
                "List price of ${plural(month.calls, "call")} and ${plural(month.verdicts, "verdict")}$unpriced",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun Figure(value: String, label: String) {
    Column {
        Text(value, style = MaterialTheme.typography.headlineSmallEmphasized, color = MaterialTheme.colorScheme.primary)
        Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

private fun plural(n: Int, word: String) = if (n == 1) "1 $word" else "$n ${word}s"
