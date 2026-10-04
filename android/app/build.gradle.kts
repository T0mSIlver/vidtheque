plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.roborazzi)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.ksp)
    alias(libs.plugins.hilt)
}

// The sign-in screen's prefilled instance: Tom's in gradle.properties, none in the public release.
val instance = providers.gradleProperty("vidtheque.instance").getOrElse("").trimEnd('/')
// The one instance whose server holds this build's Firebase key, so the only one push can reach.
val pushInstance = providers.gradleProperty("vidtheque.pushInstance").getOrElse(instance).trimEnd('/')

// CI passes the tag's X.Y.Z (android-vX.Y.Z); each part gets three digits, so a release
// always installs over the one before. Local builds stay 1 and "dev".
val release = providers.gradleProperty("vidtheque.version").orNull
val releaseCode = release?.let { v ->
    val parts = Regex("""(\d{1,3})\.(\d{1,3})\.(\d{1,3})""").matchEntire(v)?.destructured?.toList()
        ?: throw GradleException("vidtheque.version must be X.Y.Z, got $v")
    parts.fold(0) { code, part -> code * 1000 + part.toInt() }
}

android {
    namespace = "dev.vidtheque.app"
    compileSdk = 37

    defaultConfig {
        applicationId = "dev.vidtheque.app"
        minSdk = 31
        targetSdk = 36
        versionCode = releaseCode ?: 1
        versionName = release ?: "dev"
        buildConfigField("String", "INSTANCE", "\"$instance\"")
        buildConfigField("String", "PUSH_INSTANCE", "\"$pushInstance\"")
        // The Firebase project's app identity (not secrets): from the environment in CI
        // (Actions variables), from -P locally. Empty, the build has no push.
        for ((field, name) in listOf("FIREBASE_APP_ID" to "appId", "FIREBASE_API_KEY" to "apiKey", "FIREBASE_PROJECT_ID" to "projectId", "FIREBASE_PROJECT_NUMBER" to "projectNumber")) {
            val value = providers.environmentVariable(field).orElse(providers.gradleProperty("vidtheque.firebase.$name")).getOrElse("")
            buildConfigField("String", field, "\"$value\"")
        }
    }

    // CI signs with one stable key from Actions secrets, so builds install over each
    // other and the instance's assetlinks fingerprint holds. Locally: the debug key.
    signingConfigs {
        create("ci") {
            val path = System.getenv("VIDTHEQUE_KEYSTORE")
            if (path != null) {
                storeFile = file(path)
                storePassword = System.getenv("VIDTHEQUE_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("VIDTHEQUE_KEY_ALIAS")
                keyPassword = System.getenv("VIDTHEQUE_KEYSTORE_PASSWORD")
            }
        }
    }

    buildTypes {
        debug {
            // A static bearer for a local stack on VIDTHEQUE_AUTH=token; never in a release.
            buildConfigField("String", "DEV_TOKEN", "\"${providers.gradleProperty("vidtheque.devToken").getOrElse("")}\"")
            if (System.getenv("VIDTHEQUE_KEYSTORE") != null) signingConfig = signingConfigs.getByName("ci")
        }
        release {
            buildConfigField("String", "DEV_TOKEN", "\"\"")
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            // Without the CI key (a fork, a local build) release still installs, on the debug key.
            signingConfig = signingConfigs.getByName(if (System.getenv("VIDTHEQUE_KEYSTORE") != null) "ci" else "debug")
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    testOptions {
        unitTests.isIncludeAndroidResources = true
        // Robolectric's SDK 36 sandbox reaches into java.base internals.
        unitTests.all { it.jvmArgs("--add-exports=java.base/jdk.internal.access=ALL-UNNAMED", "--add-opens=java.base/java.io=ALL-UNNAMED") }
    }
}

dependencies {
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.compose.material3)
    implementation(libs.compose.icons)
    implementation(libs.activity.compose)
    implementation(libs.core.splashscreen)
    implementation(libs.hilt.android)
    ksp(libs.hilt.compiler)
    implementation(libs.hilt.viewmodel.compose)
    implementation(libs.browser)
    implementation(libs.datastore.preferences)
    implementation(libs.lifecycle.runtime.compose)
    implementation(libs.lifecycle.viewmodel.compose)
    implementation(libs.okhttp)
    implementation(libs.serialization.json)
    implementation(libs.navigation3.runtime)
    implementation(libs.navigation3.ui)
    implementation(libs.lifecycle.viewmodel.navigation3)
    implementation(libs.coil.compose)
    implementation(libs.coil.okhttp)
    implementation(platform(libs.firebase.bom))
    implementation(libs.firebase.messaging)
    implementation(libs.coroutines.play.services)
    debugImplementation(libs.compose.ui.tooling)
    debugImplementation(libs.compose.ui.test.manifest)

    testImplementation(platform(libs.compose.bom))
    testImplementation(libs.junit)
    testImplementation(libs.robolectric)
    testImplementation(libs.compose.ui.test.junit4)
    testImplementation(libs.roborazzi)
    testImplementation(libs.roborazzi.compose)
    testImplementation(libs.roborazzi.junit.rule)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.coroutines.test)
}
