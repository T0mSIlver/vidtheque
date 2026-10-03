package dev.vidtheque.app.ui

import androidx.compose.animation.EnterTransition
import androidx.compose.animation.ExitTransition
import androidx.compose.animation.ExperimentalSharedTransitionApi
import androidx.compose.animation.SharedTransitionLayout
import androidx.compose.animation.SharedTransitionScope
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.ui.Alignment
import androidx.compose.ui.graphics.Shape
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
import dev.vidtheque.app.ui.feed.Lift
import dev.vidtheque.app.ui.feed.Still
import dev.vidtheque.app.ui.profile.ProfileScreen
import dev.vidtheque.app.ui.video.VideoScreen
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.serialization.Serializable
import androidx.compose.runtime.LaunchedEffect
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

/**
 * A feed card and the video page it opens are one container (Material's container
 * transform): the page grows out of the card, and a back gesture shrinks it into
 * the card with the gesture's progress. Scaled, not remeasured, so the text keeps
 * its layout while it shrinks. A tween, not a spring: a seek maps the gesture's
 * progress onto time, and a spring would cover most of the way in the first few
 * percent. Each side stays opaque until the last third, then the two fade
 * through, so the page never shows its text over the card's.
 */
@OptIn(ExperimentalSharedTransitionApi::class)
val sharedCard: Lift = { id, shape ->
    val shared = LocalShared.current
    val scope = LocalNavAnimatedContentScope.current
    if (shared == null) Modifier else with(shared) {
        Modifier.sharedBounds(
            rememberSharedContentState("card-$id"),
            animatedVisibilityScope = scope,
            enter = fadeIn(tween(FADE_MS, delayMillis = BOUNDS_MS - FADE_MS)),
            exit = fadeOut(tween(FADE_MS, delayMillis = BOUNDS_MS - 2 * FADE_MS)),
            boundsTransform = { _, _ -> tween(BOUNDS_MS, easing = FastOutSlowInEasing) },
            resizeMode = SharedTransitionScope.ResizeMode.scaleToBounds(ContentScale.FillWidth, Alignment.TopCenter),
            clipInOverlayDuringTransition = OverlayClip(shape),
        )
    }
}

private const val BOUNDS_MS = 450
private const val FADE_MS = 120

// The container transform is the whole motion: the feed stays put underneath, whole,
// instead of fading in over the page (Navigation 3's default pop).
private val containerOnly = NavDisplay.transitionSpec { EnterTransition.None togetherWith ExitTransition.KeepUntilTransitionsFinished } +
    NavDisplay.popTransitionSpec { (EnterTransition.None togetherWith ExitTransition.KeepUntilTransitionsFinished).apply { targetContentZIndex = -1f } } +
    NavDisplay.predictivePopTransitionSpec { (EnterTransition.None togetherWith ExitTransition.KeepUntilTransitionsFinished).apply { targetContentZIndex = -1f } }

@OptIn(ExperimentalSharedTransitionApi::class)
@Composable
fun SignedIn(opening: MutableStateFlow<String?>, onSignOut: () -> Unit) {
    val stack = rememberNavBackStack(FeedKey)
    // A tapped notification lands on its video, over the feed.
    val open by opening.collectAsStateWithLifecycle()
    LaunchedEffect(open) {
        val id = open ?: return@LaunchedEffect
        opening.value = null
        stack.add(VideoKey(id))
    }
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
                            card = sharedCard,
                            actions = {
                                IconButton(onClick = { stack.add(ProfileKey) }) { Icon(Icons.Rounded.AccountCircle, contentDescription = "Your interests") }
                            },
                        )
                    }
                    entry<ProfileKey> { ProfileScreen(onBack = { stack.removeLastOrNull() }, onSignOut = onSignOut) }
                    entry<VideoKey>(metadata = containerOnly) { key -> VideoScreen(key, onBack = { stack.removeLastOrNull() }, still = sharedStill, card = sharedCard) }
                },
            )
        }
    }
}
