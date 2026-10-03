package dev.vidtheque.app.ui

import androidx.compose.animation.ExperimentalSharedTransitionApi
import androidx.compose.animation.SharedTransitionLayout
import androidx.compose.animation.SharedTransitionScope
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.AccountCircle
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.hilt.lifecycle.viewmodel.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.navigation3.rememberViewModelStoreNavEntryDecorator
import androidx.navigation3.runtime.NavKey
import androidx.navigation3.runtime.entryProvider
import androidx.navigation3.runtime.rememberNavBackStack
import androidx.navigation3.runtime.rememberSaveableStateHolderNavEntryDecorator
import androidx.navigation3.ui.LocalNavAnimatedContentScope
import androidx.navigation3.ui.NavDisplay
import coil3.compose.AsyncImage
import dev.vidtheque.app.ui.feed.FeedScreen
import dev.vidtheque.app.ui.feed.FeedViewModel
import dev.vidtheque.app.ui.feed.Still
import dev.vidtheque.app.ui.profile.ProfileScreen
import dev.vidtheque.app.ui.video.VideoScreen
import kotlinx.serialization.Serializable
import androidx.compose.ui.layout.ContentScale

@Serializable
data object FeedKey : NavKey

/** [title] and [channel] let the screen draw at once while the verdict loads; a push carries only the id. */
@Serializable
data class VideoKey(val videoId: String, val title: String = "", val channel: String = "") : NavKey

@Serializable
data object ProfileKey : NavKey

@OptIn(ExperimentalSharedTransitionApi::class)
private val LocalShared = staticCompositionLocalOf<SharedTransitionScope?> { null }

/** The still as a shared element: it lifts from the feed row into the video screen. */
@OptIn(ExperimentalSharedTransitionApi::class)
val sharedStill: Still = { id, modifier ->
    val shared = LocalShared.current
    val scope = LocalNavAnimatedContentScope.current
    val lifted = if (shared == null) modifier else with(shared) {
        modifier.sharedElement(rememberSharedContentState("still-$id"), animatedVisibilityScope = scope)
    }
    AsyncImage(model = thumbnail(id), contentDescription = null, contentScale = ContentScale.Crop, modifier = lifted)
}

@OptIn(ExperimentalSharedTransitionApi::class)
@Composable
fun SignedIn(onSignOut: () -> Unit) {
    val stack = rememberNavBackStack(FeedKey)
    SharedTransitionLayout {
        CompositionLocalProvider(LocalShared provides this) {
            NavDisplay(
                backStack = stack,
                entryDecorators = listOf(rememberSaveableStateHolderNavEntryDecorator(), rememberViewModelStoreNavEntryDecorator()),
                sharedTransitionScope = this,
                entryProvider = entryProvider {
                    entry<FeedKey> {
                        val feed: FeedViewModel = hiltViewModel()
                        val ui by feed.ui.collectAsStateWithLifecycle()
                        FeedScreen(
                            ui = ui,
                            onRefresh = feed::refresh,
                            onMore = feed::more,
                            onToggleSkipped = feed::toggleSkipped,
                            onMoreSkipped = feed::moreSkipped,
                            onOpen = { stack.add(VideoKey(it.videoId, it.title, it.channel.orEmpty())) },
                            still = sharedStill,
                            actions = {
                                IconButton(onClick = { stack.add(ProfileKey) }) { Icon(Icons.Rounded.AccountCircle, contentDescription = "Your interests") }
                            },
                        )
                    }
                    entry<ProfileKey> { ProfileScreen(onBack = { stack.removeLastOrNull() }, onSignOut = onSignOut) }
                    entry<VideoKey> { key -> VideoScreen(key, onBack = { stack.removeLastOrNull() }, still = sharedStill) }
                },
            )
        }
    }
}
