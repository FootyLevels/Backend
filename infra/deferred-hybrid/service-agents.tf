# Create service agents explicitly so first-time queue/scheduler OIDC works.
# These roles belong only to Google's service agents, never application identities.
resource "google_project_service_identity" "tasks" {
  provider   = google-beta
  project    = var.project_id
  service    = "cloudtasks.googleapis.com"
  depends_on = [google_project_service.required]
}

resource "google_project_iam_member" "tasks_service_agent" {
  project = var.project_id
  role    = "roles/cloudtasks.serviceAgent"
  member  = "serviceAccount:${google_project_service_identity.tasks.email}"
}

resource "google_project_service_identity" "scheduler" {
  provider   = google-beta
  project    = var.project_id
  service    = "cloudscheduler.googleapis.com"
  depends_on = [google_project_service.required]
}

resource "google_project_iam_member" "scheduler_service_agent" {
  project = var.project_id
  role    = "roles/cloudscheduler.serviceAgent"
  member  = "serviceAccount:${google_project_service_identity.scheduler.email}"
}
