package dev.vidtheque.app.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dev.vidtheque.app.auth.Session
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class RootViewModel @Inject constructor(private val session: Session) : ViewModel() {
    val state = session.state

    fun signOut() {
        viewModelScope.launch { session.signOut() }
    }
}
