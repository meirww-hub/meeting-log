plugins {
    id("com.android.application")
}

android {
    namespace = "com.meirww.meetingscribe"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.meirww.meetingscribe"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0-mvp"

        // כתובת שרת העיבוד - פרוס ב-Cloud Run, זמין מכל מקום ללא תלות במחשב.
        buildConfigField("String", "BACKEND_BASE_URL", "\"https://meeting-log-backend-331659377626.me-west1.run.app\"")
        // מפתח שיתופי שנשלח בכותרת X-API-Key, תואם ל-BACKEND_API_KEY בשרת.
        buildConfigField("String", "BACKEND_API_KEY", "\"e54d8bfd0db4146d43664844396b61ebf0d6c4d23f112393322f68ccd2a07f61\"")
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    buildFeatures {
        viewBinding = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("androidx.recyclerview:recyclerview:1.3.2")
    implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")
    implementation("androidx.activity:activity-ktx:1.9.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.4")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("androidx.work:work-runtime-ktx:2.9.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.google.android.gms:play-services-auth:21.2.0")
    // Shizuku - הרשאות shell לקריאת הקלטות cally מתיקייה חסומה. ראה ShizukuAccess.
    implementation("dev.rikka.shizuku:api:13.1.5")
    implementation("dev.rikka.shizuku:provider:13.1.5")

    // בדיקות JVM טהורות (ללא מכשיר/אמולטור): ./gradlew testDebugUnitTest
    testImplementation("junit:junit:4.13.2")
}
