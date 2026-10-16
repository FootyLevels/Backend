locals {
  prefix = "footylevels-${var.environment}"
  labels = { application = "footylevels", environment = var.environment, managed_by = "terraform" }
  services = toset([
    "artifactregistry.googleapis.com", "cloudresourcemanager.googleapis.com",
    "cloudscheduler.googleapis.com", "cloudtasks.googleapis.com",
    "firebase.googleapis.com", "firebaserules.googleapis.com", "firestore.googleapis.com",
    "iam.googleapis.com", "iamcredentials.googleapis.com", "identitytoolkit.googleapis.com",
    "logging.googleapis.com", "monitoring.googleapis.com", "pubsub.googleapis.com",
    "run.googleapis.com", "secretmanager.googleapis.com", "serviceusage.googleapis.com",
    "storage.googleapis.com", "sts.googleapis.com",
  ])
}

resource "google_project_service" "required" {
  for_each           = local.services
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# Import an existing Firebase project before applying; do not recreate the iOS app.
resource "google_firebase_project" "app" {
  provider   = google-beta
  project    = var.project_id
  depends_on = [google_project_service.required]
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

resource "google_storage_bucket" "media" {
  project                     = var.project_id
  name                        = "${var.project_id}-${var.environment}-private-media"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = local.labels
  soft_delete_policy { retention_duration_seconds = 0 }
  lifecycle_rule {
    action { type = "Delete" }
    condition { age = var.media_retention_days }
  }
  depends_on = [google_project_service.required]
  lifecycle { prevent_destroy = true }
}

resource "google_storage_bucket" "references" {
  project                     = var.project_id
  name                        = "${var.project_id}-${var.environment}-references"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = local.labels
  soft_delete_policy { retention_duration_seconds = 0 }
  depends_on = [google_project_service.required]
  lifecycle { prevent_destroy = true }
}

resource "google_artifact_registry_repository" "containers" {
  project       = var.project_id
  location      = var.region
  repository_id = local.prefix
  format        = "DOCKER"
  description   = "CPU application and DGX worker images; no model weights or user media."
  labels        = local.labels
  depends_on    = [google_project_service.required]
  lifecycle { prevent_destroy = true }
}

resource "google_pubsub_topic" "reconstruction" {
  project = var.project_id
  name    = "${local.prefix}-reconstruction"
  labels  = local.labels
  message_storage_policy { allowed_persistence_regions = [var.region] }
  depends_on = [google_project_service.required]
}

resource "google_pubsub_subscription" "dgx" {
  project                    = var.project_id
  name                       = "${local.prefix}-dgx"
  topic                      = google_pubsub_topic.reconstruction.id
  ack_deadline_seconds       = 60
  message_retention_duration = "604800s"
  retain_acked_messages      = false
  expiration_policy { ttl = "" }
  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
  labels = local.labels
  # No push endpoint: DGX initiates StreamingPull. FP-53 supplies leases and retries.
}

resource "google_cloud_tasks_queue" "media" {
  project         = var.project_id
  name            = "${local.prefix}-media"
  location        = var.region
  deletion_policy = "PREVENT"
  rate_limits {
    max_concurrent_dispatches = 2
    max_dispatches_per_second = 1
  }
  retry_config {
    max_attempts       = 5
    max_retry_duration = "3600s"
    min_backoff        = "10s"
    max_backoff        = "300s"
    max_doublings      = 5
  }
  depends_on = [google_project_service.required]
}
