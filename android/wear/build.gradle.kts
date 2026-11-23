plugins {
    id("com.android.application")
}

android {
    // ה-namespace שונה מ-:app, אבל ה-applicationId זהה בכוונה: ה-Data Layer של
    // Google מחבר רק בין אפליקציות עם אותו package ואותה חתימה. זה גם מה שמונע
    // מאפליקציה זרה לשלוח לטלפון פקודת הקלטה.
    namespace = "com.meirww.meetingscribe.wear"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.meirww.meetingscribe"
        // Wear OS 3 ומעלה: כל השעונים החדשים (Galaxy Watch 4+, Pixel Watch,
        // TicWatch, OnePlus). Galaxy Watch Ultra 2025 רץ על Wear OS 5.
        minSdk = 30
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    // אותו קובץ פרוטוקול כמו בטלפון - ראה android/shared.
    sourceSets["main"].kotlin.srcDir("../shared")
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("com.google.android.gms:play-services-wearable:18.2.0")
    implementation("androidx.wear.tiles:tiles:1.4.1")
    implementation("androidx.wear.protolayout:protolayout:1.2.1")
    implementation("com.google.guava:guava:33.2.1-android")

    testImplementation("junit:junit:4.13.2")
}
