plugins { kotlin("jvm") version "2.2.10" }
repositories { mavenCentral() }
dependencies { testImplementation("junit:junit:4.13.2") }
kotlin {
    jvmToolchain(17)
    sourceSets {
        main { kotlin.srcDir("voice"); kotlin.include("WakeGate.kt") }
        test { kotlin.srcDir("tests") }
    }
}
tasks.test { useJUnit(); testLogging { events("passed", "failed", "skipped") } }
