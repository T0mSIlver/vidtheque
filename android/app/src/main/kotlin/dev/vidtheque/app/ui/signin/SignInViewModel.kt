package dev.vidtheque.app.ui.signin

import android.net.Uri
import androidx.browser.auth.AuthTabIntent
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import dev.vidtheque.app.auth.Instance
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
    val host: String get() = instance.host

    private var pending: PendingSignIn?
        get() = saved.get<String>(PENDING)?.let { Json.decodeFromString(it) }
        set(value) { saved[PENDING] = value?.let { Json.encodeToString(PendingSignIn.serializer(), it) } }

    /** Ask the server where to sign in, then hand the URL to [open] (the Auth Tab). */
    fun start(open: (Uri, host: String, path: String) -> Unit) {
        if (_ui.value.busy) return
        _ui.value = SignInUi(busy = true)
        viewModelScope.launch {
            try {
                val next = session.begin()
                pending = next
                open(Uri.parse(next.url), instance.host, instance.callbackPath)
            } catch (e: Exception) {
                fail(e)
            }
        }
    }

    fun onAuthTabResult(result: AuthTabIntent.AuthResult) {
        when (result.resultCode) {
            AuthTabIntent.RESULT_OK -> result.resultUri?.let(::onRedirect) ?: fail(null)
            AuthTabIntent.RESULT_VERIFICATION_FAILED, AuthTabIntent.RESULT_VERIFICATION_TIMED_OUT -> {
                pending = null
                _ui.value = SignInUi(error = "Android could not confirm that ${instance.host} trusts this app. Its assetlinks.json must list the app's signing key.")
            }
            // Closed by the reader, or a browser without Auth Tab that will answer through the App Link.
            else -> if (result.resultCode == AuthTabIntent.RESULT_CANCELED) _ui.value = SignInUi()
        }
    }

    /** The callback URL, from the Auth Tab or from the App Link in a plain browser. */
    fun onRedirect(uri: Uri) {
        val attempt = pending ?: return
        pending = null
        _ui.value = SignInUi(busy = true)
        viewModelScope.launch {
            try {
                session.complete(uri, attempt)
                _ui.value = SignInUi()
            } catch (e: Exception) {
                fail(e)
            }
        }
    }

    private fun fail(e: Exception?) {
        _ui.value = SignInUi(
            error = when (e) {
                is OAuthException -> e.message
                is IOException -> "${instance.host} did not answer. Check the connection and try again."
                else -> "Sign-in failed. Try again."
            },
        )
    }

    private companion object {
        const val PENDING = "pending"
    }
}
