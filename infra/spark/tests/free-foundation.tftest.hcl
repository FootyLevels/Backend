mock_provider "google" {}
mock_provider "google-beta" {}

variables {
  project_id  = "footylevels-test-project"
  environment = "dev"
}

run "free_foundation" {
  command = apply
  assert {
    condition     = google_project_iam_custom_role.auth_state.permissions == toset(["firebaseauth.users.get"]) && google_project_iam_member.api_auth_state.project == var.project_id
    error_message = "Token revocation checks require user reads only, never Firebase Auth administration."
  }
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
  assert {
    condition     = google_project_iam_member.api_records.role == "roles/datastore.user" && google_project_iam_member.api_records.project == var.project_id && google_service_account.api.account_id == "footylevels-api-dev"
    error_message = "The API must receive data access only, scoped to its own environment."
  }
}

run "demo_identity_is_separate" {
  command = plan
  variables {
    project_id  = "footylevels-test-demo"
    environment = "demo"
  }
  assert {
    condition     = google_service_account.api.account_id == "footylevels-api-demo" && google_project_iam_member.api_records.project == "footylevels-test-demo" && google_service_account.api.project == "footylevels-test-demo"
    error_message = "Demo must not reuse the development API identity or project."
  }
}

run "reject_example_id" {
  command = plan
  variables { project_id = "REPLACE_WITH_PROJECT_ID" }
  expect_failures = [var.project_id]
}
