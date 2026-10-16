mock_provider "google" {
  mock_resource "google_cloud_run_v2_service" {
    defaults = { uri = "https://mock-worker.run.app" }
  }
}
mock_provider "google-beta" {}

# Distinct mock identities make permission assignment checks meaningful.
override_resource {
  target = google_service_account.runtime["api"]
  values = {
    name  = "projects/footylevels-test-project/serviceAccounts/api-worker@footylevels-test-project.iam.gserviceaccount.com"
    email = "api-worker@footylevels-test-project.iam.gserviceaccount.com"
  }
}
override_resource {
  target = google_service_account.runtime["media"]
  values = {
    name  = "projects/footylevels-test-project/serviceAccounts/media-worker@footylevels-test-project.iam.gserviceaccount.com"
    email = "media-worker@footylevels-test-project.iam.gserviceaccount.com"
  }
}
override_resource {
  target = google_service_account.runtime["dgx"]
  values = {
    name  = "projects/footylevels-test-project/serviceAccounts/dgx-worker@footylevels-test-project.iam.gserviceaccount.com"
    email = "dgx-worker@footylevels-test-project.iam.gserviceaccount.com"
  }
}
override_resource {
  target = google_service_account.runtime["tasks"]
  values = {
    name  = "projects/footylevels-test-project/serviceAccounts/tasks-worker@footylevels-test-project.iam.gserviceaccount.com"
    email = "tasks-worker@footylevels-test-project.iam.gserviceaccount.com"
  }
}
override_resource {
  target = google_service_account.runtime["scheduler"]
  values = {
    name  = "projects/footylevels-test-project/serviceAccounts/scheduler-worker@footylevels-test-project.iam.gserviceaccount.com"
    email = "scheduler-worker@footylevels-test-project.iam.gserviceaccount.com"
  }
}

variables {
  project_id                    = "footylevels-test-project"
  environment                   = "dev"
  acknowledge_billable_services = true
  region                        = "northamerica-northeast1"
}

run "private_foundation" {
  command = apply

  assert {
    condition = alltrue([
      google_storage_bucket.media.public_access_prevention == "enforced",
      google_storage_bucket.references.public_access_prevention == "enforced",
      google_storage_bucket.media.uniform_bucket_level_access,
      google_storage_bucket.references.uniform_bucket_level_access,
      !google_storage_bucket.media.force_destroy,
      google_storage_bucket.media.soft_delete_policy[0].retention_duration_seconds == 0,
    ])
    error_message = "Media must remain private, with explicit deletion behavior."
  }
  assert {
    condition     = length(google_cloud_run_v2_service.api) == 0 && length(google_cloud_run_v2_service.media) == 0 && length(google_cloud_scheduler_job.reconcile) == 0 && length(google_cloud_run_v2_service_iam_member.api_public) == 0
    error_message = "Foundation alone must not launch containers or expose the API."
  }
  assert {
    condition     = length(google_service_account_iam_member.dgx_federation) == 0
    error_message = "Unconfigured DGX external identities must have no access."
  }
  assert {
    condition     = length(google_pubsub_subscription.dgx.push_config) == 0 && google_pubsub_subscription.dgx.expiration_policy[0].ttl == ""
    error_message = "DGX must pull jobs, and its subscription must survive inactivity."
  }
  assert {
    condition     = google_pubsub_subscription_iam_member.pull.role == "roles/pubsub.subscriber" && google_pubsub_subscription_iam_member.pull.member == "serviceAccount:${google_service_account.runtime["dgx"].email}" && google_storage_bucket_iam_member.media_cleanup.member != google_storage_bucket_iam_member.media_create["dgx"].member && !contains([for grant in google_cloud_tasks_queue_iam_member.enqueue : grant.member], "serviceAccount:${google_service_account.runtime["dgx"].email}")
    error_message = "DGX access must exclude media dispatch and object deletion."
  }
  assert {
    condition     = !contains(local.services, "compute.googleapis.com") && google_firestore_database.app.delete_protection_state == "DELETE_PROTECTION_ENABLED"
    error_message = "Do not introduce a cloud GPU or allow accidental database destruction."
  }
}

run "demo_isolation" {
  command = apply
  variables {
    project_id  = "footylevels-demo-test"
    environment = "demo"
  }
  assert {
    condition     = google_storage_bucket.media.name != run.private_foundation.application_environment.GCS_MEDIA_BUCKET && google_pubsub_subscription.dgx.name == "footylevels-demo-dgx"
    error_message = "Dev and demo must resolve to different media and queue resources."
  }
}

run "private_services" {
  command = apply
  variables {
    api_image   = "northamerica-northeast2-docker.pkg.dev/footylevels-test-project/footylevels-dev/api-worker@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    media_image = "northamerica-northeast2-docker.pkg.dev/footylevels-test-project/footylevels-dev/media-worker@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  }
  assert {
    condition     = length(google_cloud_run_v2_service_iam_member.api_public) == 0 && google_cloud_run_v2_service.media[0].ingress == "INGRESS_TRAFFIC_INTERNAL_ONLY" && keys(google_cloud_run_v2_service_iam_member.media_invoke) == ["scheduler", "tasks"]
    error_message = "Images alone must not publish the API, and only task/scheduler identities may invoke media."
  }
  assert {
    condition     = google_cloud_run_v2_service.media[0].template[0].max_instance_request_concurrency == 1 && google_cloud_run_v2_service.media[0].template[0].scaling[0].min_instance_count == 0 && google_cloud_run_v2_service.media[0].template[0].scaling[0].max_instance_count == 2 && google_cloud_scheduler_job.reconcile[0].paused
    error_message = "Media capacity must be bounded and reconciliation must require deliberate activation."
  }
}

run "reject_mutable_image" {
  command = plan
  variables { api_image = "example/api:latest" }
  expect_failures = [var.api_image]
}

run "reject_pool_wildcard" {
  command = plan
  variables { dgx_federated_principal = "principalSet://iam.googleapis.com/projects/123/locations/global/workloadIdentityPools/university/*" }
  expect_failures = [var.dgx_federated_principal]
}

run "reject_unfilled_project" {
  command = plan
  variables { project_id = "REPLACE_WITH_DEV_PROJECT_ID" }
  expect_failures = [var.project_id]
}

run "reject_unapproved_billable_stack" {
  command = plan
  variables { acknowledge_billable_services = false }
  expect_failures = [var.acknowledge_billable_services]
}
