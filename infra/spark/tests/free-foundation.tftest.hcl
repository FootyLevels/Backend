mock_provider "google" {}
mock_provider "google-beta" {}

variables {
  project_id  = "footylevels-test-project"
  environment = "dev"
}

run "free_foundation" {
  command = apply
  assert {
    condition     = !contains(local.services, "run.googleapis.com") && !contains(local.services, "storage.googleapis.com") && !contains(local.services, "pubsub.googleapis.com") && !contains(local.services, "cloudtasks.googleapis.com") && !contains(local.services, "artifactregistry.googleapis.com")
    error_message = "The active configuration must not enable the deferred hosting/storage/queue stack."
  }
  assert {
    condition     = google_firestore_database.app.name == "(default)" && google_firestore_database.app.point_in_time_recovery_enablement == "POINT_IN_TIME_RECOVERY_DISABLED" && google_firestore_database.app.delete_protection_state == "DELETE_PROTECTION_ENABLED"
    error_message = "Use one protected free-quota database, with no paid PITR."
  }
  assert {
    condition     = strcontains(google_firebaserules_ruleset.firestore.source[0].files[0].content, "allow read, write: if false;")
    error_message = "Client access must remain denied until server authorization exists."
  }
  assert {
    condition     = google_firebase_apple_app.ios.bundle_id == "ca.sheridan.FootyLevels-Frontend" && google_firebase_apple_app.ios.deletion_policy == "PREVENT"
    error_message = "Register the existing iOS bundle separately in each project and protect it from deletion."
  }
}

run "reject_example_id" {
  command = plan
  variables { project_id = "REPLACE_WITH_PROJECT_ID" }
  expect_failures = [var.project_id]
}
