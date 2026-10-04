package dev.vidtheque.app.ui.signin

import android.net.Uri
import androidx.browser.auth.AuthTabIntent
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dev.vidtheque.app.auth.Instance
import dev.vidtheque.app.auth.InvalidInstance
import dev.vidtheque.app.auth.OAuthException
import dev.vidtheque.app.auth.PendingSignIn
import dev.vidtheque.app.auth.Session
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import java.io.IOException
import javax.inject.Inject

data class SignInUi(val busy: Boolean = false, val error: String? = null)

@HiltViewModel
class SignInViewModel @Inject constructor(
    private val session: Session,
    private val instance: Instance,
    // The attempt in flight survives the process being killed while the browser is up.
    private val saved: SavedStateHandle,
) : ViewModel() {
    private val _ui = MutableStateFlow(SignInUi())
    val ui: StateFlow<SignInUi> = _ui.asStateFlow()

    /** What the reader typed; null shows [current]. */
    val typed: StateFlow<String?> = saved.getStateFlow(TYPED, null)

    /** The last instance signed in to, else the build's default (empty in the public release). */
    val current: String get() = instance.base

    fun type(text: String) {
        saved[TYPED] = text
        if (_ui.value.error != null) _ui.value = _ui.value.copy(error = null)
    }

    private var pending: PendingSignIn?
        get() = saved.get<String>(PENDING)?.let { Json.decodeFromString(it) }
        set(value) { saved[PENDING] = value?.let { Json.encodeToString(PendingSignIn.serializer(), it) } }

    /** Check the typed instance, ask it where to sign in, then hand the URL to [open] (the Auth Tab). */
    fun start(open: (Uri) -> Unit) {
        if (_ui.value.busy) return
        val base = try {
            Instance.normalize(typed.value ?: instance.base)
        } catch (e: InvalidInstance) {
            _ui.value = SignInUi(error = e.message)
            return
        }
        _ui.value = SignInUi(busy = true)
        viewModelScope.launch {
            try {
                val next = session.begin(base)
                pending = next
                open(Uri.parse(next.url))
            } catch (e: Exception) {
                fail(e, base)
            }
        }
    }

    fun onAuthTabResult(result: AuthTabIntent.AuthResult) {
        when (result.resultCode) {
            AuthTabIntent.RESULT_OK -> result.resultUri?.let(::onRedirect) ?: fail(null, pending?.instance)
            // Closed by the reader, or a browser without Auth Tab that will answer through the intent filter.
            else -> if (result.resultCode == AuthTabIntent.RESULT_CANCELED) _ui.value = SignInUi()
        }
    }

    /** The callback URL, from the Auth Tab or, in a browser without it, from the intent filter. */
    fun onRedirect(uri: Uri) {
        val attempt = pending ?: return
        pending = null
        _ui.value = SignInUi(busy = true)
        viewModelScope.launch {
            try {
                session.complete(uri, attempt)
                saved[TYPED] = null
                _ui.value = SignInUi()
            } catch (e: Exception) {
                fail(e, attempt.instance)
            }
        }
    }

    private fun fail(e: Exception?, base: String?) {
        _ui.value = SignInUi(
            error = when (e) {
                is OAuthException -> e.message
                is IOException -> "${base?.let(Instance::hostOf) ?: "The instance"} did not answer. Check the address and the connection."
                else -> "Sign-in failed. Try again."
            },
        )
    }

    private companion object {
        const val PENDING = "pending"
        const val TYPED = "typed"
    }
}
