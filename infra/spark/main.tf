locals {
  services = toset([
    "cloudresourcemanager.googleapis.com", "serviceusage.googleapis.com",
    "firebase.googleapis.com", "firestore.googleapis.com",
    "firebaserules.googleapis.com", "identitytoolkit.googleapis.com",
    "iam.googleapis.com",
  ])
}

resource "google_project_service" "required" {
  for_each           = local.services
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_firebase_project" "app" {
  provider   = google-beta
  project    = var.project_id
  depends_on = [google_project_service.required]
  lifecycle { prevent_destroy = true }
}

resource "google_firebase_apple_app" "ios" {
  provider        = google-beta
  project         = var.project_id
  display_name    = "FootyLevels ${var.environment}"
  bundle_id       = "ca.sheridan.FootyLevels-Frontend"
  deletion_policy = "PREVENT"
  depends_on      = [google_firebase_project.app]
  lifecycle { prevent_destroy = true }
}

resource "google_firestore_database" "app" {
  project                           = var.project_id
  name                              = "(default)"
  location_id                       = var.region
  type                              = "FIRESTORE_NATIVE"
  delete_protection_state           = "DELETE_PROTECTION_ENABLED"
  deletion_policy                   = "PREVENT"
  point_in_time_recovery_enablement = "POINT_IN_TIME_RECOVERY_DISABLED"
  depends_on                        = [google_firebase_project.app]
  lifecycle { prevent_destroy = true }
}

resource "google_firebaserules_ruleset" "firestore" {
  provider = google-beta
  project  = var.project_id
  source {
    files {
      name    = "firestore.rules"
      content = file("${path.module}/firestore.rules")
    }
  }
  depends_on = [google_firestore_database.app]
}

resource "google_firebaserules_release" "firestore" {
  provider     = google-beta
  project      = var.project_id
  name         = "cloud.firestore"
  ruleset_name = google_firebaserules_ruleset.firestore.name
}

output "application_environment" {
  value = {
    GOOGLE_CLOUD_PROJECT    = var.project_id
    FIREBASE_PROJECT_ID     = var.project_id
    FIREBASE_IOS_APP_ID     = google_firebase_apple_app.ios.app_id
    FIRESTORE_DATABASE      = google_firestore_database.app.name
    FIRESTORE_API_IDENTITY  = google_service_account.api.email
    FOOTYLEVELS_ENVIRONMENT = var.environment
  }
}
