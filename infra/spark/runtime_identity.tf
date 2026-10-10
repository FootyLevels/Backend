# The DGX API owns authorization; Firebase client rules do not apply to this identity.
# Keep credentials outside Terraform so no private key is stored in its state.
resource "google_service_account" "api" {
  project      = var.project_id
  account_id   = "footylevels-api-${var.environment}"
  display_name = "FootyLevels ${var.environment} API"
  description  = "DGX records and token account-state verification; no administration"
  depends_on   = [google_project_service.required]
}

resource "google_project_iam_member" "api_records" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.api.email}"
}

# check_revoked=True reads account status/revocation time. It must never grant
# the application permission to create users, change credentials or send email.
resource "google_project_iam_custom_role" "auth_state" {
  project     = var.project_id
  role_id     = "footylevelsAuthState"
  title       = "FootyLevels token account-state reader"
  description = "Read Firebase users for disabled-account and token-revocation checks"
  permissions = ["firebaseauth.users.get"]
}

resource "google_project_iam_member" "api_auth_state" {
  project = var.project_id
  role    = google_project_iam_custom_role.auth_state.name
  member  = "serviceAccount:${google_service_account.api.email}"
}
