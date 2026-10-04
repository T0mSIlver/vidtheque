package dev.vidtheque.app.ui.week

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dev.vidtheque.app.data.Api
import dev.vidtheque.app.data.ApiException
import dev.vidtheque.app.data.FittedWeek
import dev.vidtheque.app.data.OutsideWeek
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.io.IOException
import javax.inject.Inject

/** [asked] is the week shown, null for the current one; [week] the last one read. */
data class WeekUi(
    val asked: String? = null,
    val week: FittedWeek? = null,
    /** The week's picks from outside the follows; null until read, and when the read failed. */
    val outside: OutsideWeek? = null,
    val refreshing: Boolean = false,
    val error: String? = null,
    val budgetFailed: Boolean = false,
)

@HiltViewModel
class WeekViewModel @Inject constructor(private val api: Api) : ViewModel() {
    private val _ui = MutableStateFlow(WeekUi())
    val ui: StateFlow<WeekUi> = _ui.asStateFlow()
    private var reading: Job? = null

    fun refresh() {
        reading?.cancel()
        val asked = _ui.value.asked
        _ui.update { it.copy(refreshing = true, error = null) }
        reading = viewModelScope.launch {
            try {
                val week = api.week(asked)
                // The band is the week's: another week's never stays under this one.
                _ui.update { it.copy(week = week, outside = null) }
                // Its own read, after the week's: a failure here only hides the band.
                val outside = try {
                    api.outside(week.week)
                } catch (_: ApiException) {
                    null
                } catch (_: IOException) {
                    null
                }
                _ui.update { it.copy(outside = outside) }
            } catch (e: ApiException) {
                _ui.update { it.copy(error = e.message) }
            } catch (e: IOException) {
                _ui.update { it.copy(error = "The instance did not answer. Pull to try again.") }
            }
            _ui.update { it.copy(refreshing = false) }
        }
    }

    /** Another week; the current one is asked for as null. */
    fun go(week: String?) {
        _ui.update { it.copy(asked = week) }
        refresh()
    }

    /** The budget is weekly and set as minutes a day. */
    fun budget(perDay: Int) {
        viewModelScope.launch {
            try {
                api.budget(perDay * 7)
                _ui.update { it.copy(budgetFailed = false) }
                refresh()
            } catch (_: ApiException) {
                _ui.update { it.copy(budgetFailed = true) }
            } catch (_: IOException) {
                _ui.update { it.copy(budgetFailed = true) }
            }
        }
    }

    /** Forget this session's week (sign-out). */
    fun clear() {
        reading?.cancel()
        _ui.value = WeekUi()
    }
}
