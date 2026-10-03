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
private data class ErrorResponse(val error: String, @SerialName("error_description") val description: String? = null)

/** A refusal from the server, or a response we could not use. */
class OAuthException(val error: String, message: String) : Exception(message)

/** The three HTTP calls of a public client: discovery, code exchange, refresh. */
@Singleton
class OAuthClient @Inject constructor(
    private val http: OkHttpClient,
    private val instance: Instance,
) {
    private val json = Json { ignoreUnknownKeys = true }

    suspend fun metadata(): ServerMetadata {
        val meta = json.decodeFromString<ServerMetadata>(get("${instance.base}/.well-known/oauth-authorization-server"))
        // A metadata document naming another issuer is a mix-up; refuse it (RFC 8414 §3.3).
        if (meta.issuer.trimEnd('/') != instance.base) throw OAuthException("invalid_issuer", "The server answered as ${meta.issuer}.")
        return meta
    }

    fun authorizeUrl(meta: ServerMetadata, challenge: String, state: String): Uri =
        Uri.parse(meta.authorizationEndpoint).buildUpon()
            .appendQueryParameter("response_type", "code")
            .appendQueryParameter("client_id", instance.clientId)
            .appendQueryParameter("redirect_uri", instance.redirectUri)
            .appendQueryParameter("scope", instance.scope)
            .appendQueryParameter("state", state)
            .appendQueryParameter("code_challenge", challenge)
            .appendQueryParameter("code_challenge_method", "S256")
            .build()

    suspend fun exchange(meta: ServerMetadata, code: String, verifier: String): TokenResponse = token(
        meta,
        FormBody.Builder()
            .add("grant_type", "authorization_code")
            .add("code", code)
            .add("redirect_uri", instance.redirectUri)
            .add("client_id", instance.clientId)
            .add("code_verifier", verifier)
            .build(),
    )

    suspend fun refresh(meta: ServerMetadata, refreshToken: String): TokenResponse = token(
        meta,
        FormBody.Builder()
            .add("grant_type", "refresh_token")
            .add("refresh_token", refreshToken)
            .add("client_id", instance.clientId)
            .build(),
    )

    /** Best effort: a token the server cannot revoke expires on its own. */
    suspend fun revoke(meta: ServerMetadata, token: String) {
        val endpoint = meta.revocationEndpoint ?: return
        runCatching {
            call(Request.Builder().url(endpoint).post(FormBody.Builder().add("token", token).add("client_id", instance.clientId).build()).build())
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
