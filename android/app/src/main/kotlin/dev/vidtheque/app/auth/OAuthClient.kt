package dev.vidtheque.app.auth

import android.net.Uri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.FormBody
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.IOException
import javax.inject.Inject
import javax.inject.Singleton

@Serializable
data class ServerMetadata(
    val issuer: String,
    @SerialName("authorization_endpoint") val authorizationEndpoint: String,
    @SerialName("token_endpoint") val tokenEndpoint: String,
    @SerialName("revocation_endpoint") val revocationEndpoint: String? = null,
)

@Serializable
data class TokenResponse(
    @SerialName("access_token") val accessToken: String,
    @SerialName("refresh_token") val refreshToken: String? = null,
    @SerialName("expires_in") val expiresIn: Long = 3600,
)

@Serializable
private data class ClientDocument(@SerialName("redirect_uris") val redirectUris: List<String> = emptyList())

@Serializable
private data class ErrorResponse(val error: String, @SerialName("error_description") val description: String? = null)

/** A refusal from the server, or a response we could not use. */
class OAuthException(val error: String, message: String) : Exception(message)

/** The HTTP calls of a public client: discovery, code exchange, refresh, revocation. */
@Singleton
class OAuthClient @Inject constructor(private val http: OkHttpClient) {
    private val json = Json { ignoreUnknownKeys = true }

    /** The authorization server at [base]; a network failure stays an IOException. */
    suspend fun metadata(base: String): ServerMetadata {
        val meta = try {
            json.decodeFromString<ServerMetadata>(get("$base/.well-known/oauth-authorization-server"))
        } catch (e: Exception) {
            if (e is IOException || e is kotlinx.coroutines.CancellationException) throw e
            throw OAuthException("not_an_instance", "${Instance.hostOf(base)} does not answer as a vidtheque instance with sign-in on.")
        }
        // A metadata document naming another issuer is a mix-up; refuse it (RFC 8414 §3.3).
        if (meta.issuer.trimEnd('/') != base) throw OAuthException("invalid_issuer", "The server answered as ${meta.issuer}.")
        return meta
    }

    /** An instance from before the app scheme refuses it at /authorize, in the browser; say so here instead. */
    suspend fun checkClient(base: String) {
        val doc = try {
            json.decodeFromString<ClientDocument>(get(Instance.clientId(base)))
        } catch (e: Exception) {
            if (e is IOException || e is kotlinx.coroutines.CancellationException) throw e
            null
        }
        if (doc == null || Instance.REDIRECT_URI !in doc.redirectUris) {
            throw OAuthException("old_instance", "${Instance.hostOf(base)} runs a vidtheque too old for this app. Update it, then sign in again.")
        }
    }

    fun authorizeUrl(meta: ServerMetadata, base: String, challenge: String, state: String): Uri =
        Uri.parse(meta.authorizationEndpoint).buildUpon()
            .appendQueryParameter("response_type", "code")
            .appendQueryParameter("client_id", Instance.clientId(base))
            .appendQueryParameter("redirect_uri", Instance.REDIRECT_URI)
            .appendQueryParameter("scope", Instance.SCOPE)
            .appendQueryParameter("state", state)
            .appendQueryParameter("code_challenge", challenge)
            .appendQueryParameter("code_challenge_method", "S256")
            .build()

    suspend fun exchange(meta: ServerMetadata, base: String, code: String, verifier: String): TokenResponse = token(
        meta,
        FormBody.Builder()
            .add("grant_type", "authorization_code")
            .add("code", code)
            .add("redirect_uri", Instance.REDIRECT_URI)
            .add("client_id", Instance.clientId(base))
            .add("code_verifier", verifier)
            .build(),
    )

    suspend fun refresh(meta: ServerMetadata, base: String, refreshToken: String): TokenResponse = token(
        meta,
        FormBody.Builder()
            .add("grant_type", "refresh_token")
            .add("refresh_token", refreshToken)
            .add("client_id", Instance.clientId(base))
            .build(),
    )

    /** Best effort: a token the server cannot revoke expires on its own. */
    suspend fun revoke(meta: ServerMetadata, base: String, token: String) {
        val endpoint = meta.revocationEndpoint ?: return
        runCatching {
            // The SDK's revocation handler 400s a form without client_secret, even a public client's.
            val form = FormBody.Builder().add("token", token).add("client_id", Instance.clientId(base)).add("client_secret", "").build()
            call(Request.Builder().url(endpoint).post(form).build())
        }
    }

    private suspend fun token(meta: ServerMetadata, body: FormBody): TokenResponse =
        json.decodeFromString(call(Request.Builder().url(meta.tokenEndpoint).post(body).build()))

    private suspend fun get(url: String): String = call(Request.Builder().url(url).get().build())

    private suspend fun call(request: Request): String = withContext(Dispatchers.IO) {
        http.newCall(request).execute().use { response ->
            val text = response.body.string()
            if (response.isSuccessful) return@use text
            val refusal = runCatching { json.decodeFromString<ErrorResponse>(text) }.getOrNull()
            throw OAuthException(
                refusal?.error ?: "http_${response.code}",
                refusal?.description ?: "The server answered HTTP ${response.code}.",
            )
        }
    }
}
