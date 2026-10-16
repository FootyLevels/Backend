locals {
  runtime_env = {
    GOOGLE_CLOUD_PROJECT        = var.project_id
    FIREBASE_PROJECT_ID         = var.project_id
    FOOTYLEVELS_ENVIRONMENT     = var.environment
    FIRESTORE_DATABASE          = google_firestore_database.app.name
    GCS_MEDIA_BUCKET            = google_storage_bucket.media.name
    GCS_REFERENCE_BUCKET        = google_storage_bucket.references.name
    RECONSTRUCTION_TOPIC        = google_pubsub_topic.reconstruction.id
    RECONSTRUCTION_SUBSCRIPTION = google_pubsub_subscription.dgx.id
    MEDIA_QUEUE                 = google_cloud_tasks_queue.media.id
    MEDIA_TASK_SERVICE_ACCOUNT  = google_service_account.runtime["tasks"].email
    MEDIA_RETENTION_DAYS        = tostring(var.media_retention_days)
  }
}

resource "google_cloud_run_v2_service" "api" {
  count               = var.api_image == "" ? 0 : 1
  project             = var.project_id
  location            = var.region
  name                = "${local.prefix}-api"
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = true
  labels              = local.labels
  template {
    service_account                  = google_service_account.runtime["api"].email
    max_instance_request_concurrency = 20
    timeout                          = "60s"
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }
    containers {
      image = var.api_image
      resources {
        limits   = { cpu = "1", memory = "1Gi" }
        cpu_idle = true
      }
      dynamic "env" {
        for_each = merge(local.runtime_env, {
          MEDIA_WORKER_URL = try(google_cloud_run_v2_service.media[0].uri, "")
        })
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }
  depends_on = [
    google_project_service.required,
    google_project_iam_member.database,
    google_cloud_tasks_queue_iam_member.enqueue,
    google_service_account_iam_member.attach_task_identity,
    google_service_account_iam_member.api_sign,
    google_storage_bucket_iam_member.media_read,
    google_storage_bucket_iam_member.media_create,
    google_pubsub_topic_iam_member.publish,
  ]
}

resource "google_cloud_run_v2_service" "media" {
  count               = var.media_image == "" ? 0 : 1
  project             = var.project_id
  location            = var.region
  name                = "${local.prefix}-media"
  ingress             = "INGRESS_TRAFFIC_INTERNAL_ONLY"
  deletion_protection = true
  labels              = local.labels
  template {
    service_account                  = google_service_account.runtime["media"].email
    max_instance_request_concurrency = 1
    timeout                          = "1800s"
    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }
    containers {
      image = var.media_image
      resources {
        limits   = { cpu = "2", memory = "2Gi" }
        cpu_idle = true
      }
      dynamic "env" {
        for_each = local.runtime_env
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }
  depends_on = [
    google_project_service.required,
    google_project_iam_member.database,
    google_storage_bucket_iam_member.media_cleanup,
    google_pubsub_topic_iam_member.publish,
    google_cloud_tasks_queue_iam_member.enqueue,
    google_service_account_iam_member.attach_task_identity,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "api_public" {
  count    = var.enable_public_api && var.api_image != "" ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.api[0].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "media_invoke" {
  for_each = var.media_image == "" ? toset([]) : toset(["tasks", "scheduler"])
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.media[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.runtime[each.key].email}"
}

resource "google_cloud_scheduler_job" "reconcile" {
  count       = var.media_image == "" ? 0 : 1
  project     = var.project_id
  region      = var.region
  name        = "${local.prefix}-reconcile"
  schedule    = "*/5 * * * *"
  time_zone   = "Etc/UTC"
  paused      = true
  description = "Enable after FP-54 implements and validates idempotent reconciliation."
  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_v2_service.media[0].uri}/internal/reconcile"
    oidc_token {
      service_account_email = google_service_account.runtime["scheduler"].email
      audience              = google_cloud_run_v2_service.media[0].uri
    }
  }
  depends_on = [
    google_cloud_run_v2_service_iam_member.media_invoke,
    google_project_iam_member.scheduler_service_agent,
  ]
}
