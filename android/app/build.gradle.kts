plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.roborazzi)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.ksp)
    alias(libs.plugins.hilt)
}

val instance = providers.gradleProperty("vidtheque.instance").get().trimEnd('/')

android {
    namespace = "dev.vidtheque.app"
    compileSdk = 37

    defaultConfig {
        applicationId = "dev.vidtheque.app"
        minSdk = 31
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
        buildConfigField("String", "INSTANCE", "\"$instance\"")
        manifestPlaceholders["instanceHost"] = instance.substringAfter("://")
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
            if (System.getenv("VIDTHEQUE_KEYSTORE") != null) signingConfig = signingConfigs.getByName("ci")
        }
        release {
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
