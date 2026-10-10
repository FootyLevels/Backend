# The DGX API owns authorization; Firebase client rules do not apply to this identity.
# Keep credentials outside Terraform so no private key is stored in its state.
resource "google_service_account" "api" {
  project      = var.project_id
  account_id   = "footylevels-api-${var.environment}"
  display_name = "FootyLevels ${var.environment} API"
  description  = "DGX application records only; no project or Firebase administration"
  depends_on   = [google_project_service.required]
}

resource "google_project_iam_member" "api_records" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.api.email}"
}
