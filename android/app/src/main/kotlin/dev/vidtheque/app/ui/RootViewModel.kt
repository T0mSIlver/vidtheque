package dev.vidtheque.app.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dev.vidtheque.app.auth.Session
import dev.vidtheque.app.push.Push
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class RootViewModel @Inject constructor(private val session: Session, private val push: Push) : ViewModel() {
    val state = session.state

    /** A video a notification asked to open, for the signed-in back stack to take. */
    val opening = kotlinx.coroutines.flow.MutableStateFlow<String?>(null)

    fun signOut() {
        // Forget this phone first: the call needs the session it is about to end.
        viewModelScope.launch {
            runCatching { push.disable() }
            session.signOut()
        }
    }
}
