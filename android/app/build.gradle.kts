import java.io.ByteArrayOutputStream

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

/** How many commits deep the tree is; 1 when git cannot be asked. */
fun gitCommitCount(): Int = try {
    val out = ByteArrayOutputStream()
    exec {
        commandLine("git", "rev-list", "--count", "HEAD")
        standardOutput = out
        errorOutput = ByteArrayOutputStream()
        workingDir = rootDir
        isIgnoreExitValue = true
    }
    out.toString().trim().toIntOrNull() ?: 1
} catch (_: Exception) {
    1
}

android {
    namespace = "org.sheetmusicshelf.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "org.sheetmusicshelf.app"
        // 26 covers anything still worth playing from. Nothing here needs a
        // newer API, and the server does the rendering.
        minSdk = 26
        targetSdk = 34
        // Derived from the commit count, so every build is newer than the last
        // one without anybody remembering to bump it. Android will not install
        // an APK whose versionCode is not greater than the installed one, and
        // a version that never changes is a tablet that never updates.
        versionCode = gitCommitCount()
        versionName = "1.0.${gitCommitCount()}"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // Debug-signed so a plain `assembleRelease` still produces something
            // installable; this is a LAN client, not a Play Store upload.
            signingConfig = signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        viewBinding = true
    }
}

dependencies {
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.recyclerview:recyclerview:1.3.2")
    implementation("androidx.viewpager2:viewpager2:1.1.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("com.google.android.material:material:1.12.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
}
