resource "google_service_account" "runtime" {
  for_each     = toset(["api", "media", "dgx", "tasks", "scheduler"])
  project      = var.project_id
  account_id   = "${local.prefix}-${each.key}"
  display_name = "FootyLevels ${var.environment} ${each.key}"
  depends_on   = [google_project_service.required]
}

# Firestore server IAM is project-wide; application services must enforce ownership.
resource "google_project_iam_member" "database" {
  for_each = toset(["api", "media", "dgx"])
  project  = var.project_id
  role     = "roles/datastore.user"
  member   = "serviceAccount:${google_service_account.runtime[each.key].email}"
}

resource "google_storage_bucket_iam_member" "media_read" {
  for_each = toset(["api", "dgx"])
  bucket   = google_storage_bucket.media.name
  role     = "roles/storage.objectViewer"
  member   = "serviceAccount:${google_service_account.runtime[each.key].email}"
}

resource "google_storage_bucket_iam_member" "media_create" {
  for_each = toset(["api", "dgx"])
  bucket   = google_storage_bucket.media.name
  role     = "roles/storage.objectCreator"
  member   = "serviceAccount:${google_service_account.runtime[each.key].email}"
}

resource "google_storage_bucket_iam_member" "media_cleanup" {
  bucket = google_storage_bucket.media.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.runtime["media"].email}"
}

resource "google_storage_bucket_iam_member" "reference_read" {
  for_each = toset(["api", "dgx"])
  bucket   = google_storage_bucket.references.name
  role     = "roles/storage.objectViewer"
  member   = "serviceAccount:${google_service_account.runtime[each.key].email}"
}

resource "google_pubsub_topic_iam_member" "publish" {
  for_each = toset(["api", "media"])
  project  = var.project_id
  topic    = google_pubsub_topic.reconstruction.name
  role     = "roles/pubsub.publisher"
  member   = "serviceAccount:${google_service_account.runtime[each.key].email}"
}

resource "google_pubsub_subscription_iam_member" "pull" {
  project      = var.project_id
  subscription = google_pubsub_subscription.dgx.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.runtime["dgx"].email}"
}

resource "google_cloud_tasks_queue_iam_member" "enqueue" {
  for_each = toset(["api", "media"])
  project  = var.project_id
  location = var.region
  name     = google_cloud_tasks_queue.media.name
  role     = "roles/cloudtasks.enqueuer"
  member   = "serviceAccount:${google_service_account.runtime[each.key].email}"
}

# Enqueuers may attach only the dedicated task identity, not arbitrary accounts.
resource "google_service_account_iam_member" "attach_task_identity" {
  for_each           = toset(["api", "media"])
  service_account_id = google_service_account.runtime["tasks"].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.runtime[each.key].email}"
}

# Remote IAM signing for short-lived asset URLs; no exported private key.
resource "google_service_account_iam_member" "api_sign" {
  service_account_id = google_service_account.runtime["api"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.runtime["api"].email}"
}

resource "google_artifact_registry_repository_iam_member" "dgx_pull" {
  project    = var.project_id
  location   = var.region
  repository = google_artifact_registry_repository.containers.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.runtime["dgx"].email}"
}

resource "google_project_iam_member" "dgx_logs" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.runtime["dgx"].email}"
}

resource "google_service_account_iam_member" "dgx_federation" {
  count              = var.dgx_federated_principal == null ? 0 : 1
  service_account_id = google_service_account.runtime["dgx"].name
  role               = "roles/iam.workloadIdentityUser"
  member             = var.dgx_federated_principal
}
