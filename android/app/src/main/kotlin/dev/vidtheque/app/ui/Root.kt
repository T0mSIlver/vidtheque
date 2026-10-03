package dev.vidtheque.app.ui

import androidx.compose.animation.EnterExitState
import androidx.compose.animation.core.animateDp
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.remember
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Outline
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import dev.vidtheque.app.ui.feed.plainStill
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

/**
 * A feed card and the video page it opens are one container (Material's container
 * transform): the page grows out of the card, and a back gesture shrinks it into
 * the card with the gesture's progress. Everything, the still included, is inside
 * the container and scaled with it, not remeasured, so nothing is left behind
 * outside its bounds; the page puts its still at its top edge, where the card has
 * it, so the two land on each other. The corner goes from the card's radius to the
 * page's square one with the bounds. A tween, not a spring: a seek maps the
 * gesture's progress onto time, and a spring would cover most of the way in the
 * first few percent.
 *
 * The fades differ by direction. Opening, the card's text leaves at once and the
 * page's arrives while the container grows. Going back, the page stays opaque
 * until the last third so it follows the finger, then the card's text fades in.
 */
val sharedCard: Lift = { id, corner ->
    lift(id, rest = corner, away = 0.dp, enter = fadeIn(tween(FADE_MS, delayMillis = BOUNDS_MS - FADE_MS)), exit = fadeOut(tween(LEAVE_MS)))
}

/** The page's side of [sharedCard]: square at rest, the hero card's radius at the far end. */
val sharedPage: Lift = { id, corner ->
    lift(id, rest = corner, away = 28.dp, enter = fadeIn(tween(ARRIVE_MS, delayMillis = LEAVE_MS / 2)), exit = fadeOut(tween(FADE_MS, delayMillis = BOUNDS_MS - 2 * FADE_MS)))
}

@OptIn(ExperimentalSharedTransitionApi::class)
@Composable
private fun lift(id: String, rest: Dp, away: Dp, enter: EnterTransition, exit: ExitTransition): Modifier {
    val shared = LocalShared.current
    val scope = LocalNavAnimatedContentScope.current
    if (shared == null) return Modifier
    val corner = scope.transition.animateDp(transitionSpec = { tween(BOUNDS_MS, easing = FastOutSlowInEasing) }, label = "corner") {
        if (it == EnterExitState.Visible) rest else away
    }
    val clip = remember(corner) { RoundedClip { corner.value } }
    return with(shared) {
        Modifier.sharedBounds(
            rememberSharedContentState("card-$id"),
            animatedVisibilityScope = scope,
            enter = enter,
            exit = exit,
            boundsTransform = { _, _ -> tween(BOUNDS_MS, easing = FastOutSlowInEasing) },
            resizeMode = SharedTransitionScope.ResizeMode.scaleToBounds(ContentScale.FillWidth, Alignment.TopCenter),
            clipInOverlayDuringTransition = OverlayClip(clip),
        )
    }
}

/** A rounded rectangle whose radius is read when it is drawn, so it follows an animation. */
private class RoundedClip(private val radius: () -> Dp) : Shape {
    override fun createOutline(size: Size, layoutDirection: LayoutDirection, density: Density): Outline =
        RoundedCornerShape(radius()).createOutline(size, layoutDirection, density)
}

private const val BOUNDS_MS = 450
private const val FADE_MS = 120
private const val LEAVE_MS = 90
private const val ARRIVE_MS = 200

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
                                                        card = sharedCard,
                            actions = {
                                IconButton(onClick = { stack.add(ProfileKey) }) { Icon(Icons.Rounded.AccountCircle, contentDescription = "Your interests") }
                            },
                        )
                    }
                    entry<ProfileKey> { ProfileScreen(onBack = { stack.removeLastOrNull() }, onSignOut = onSignOut) }
                    entry<VideoKey>(metadata = containerOnly) { key -> VideoScreen(key, onBack = { stack.removeLastOrNull() }, still = plainStill, card = sharedPage) }
                },
            )
        }
    }
}
