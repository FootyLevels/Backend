# Only indexes exercised by the implemented repository, not speculative features.
resource "google_firestore_index" "owned_projects" {
  project     = var.project_id
  database    = google_firestore_database.app.name
  collection  = "projects"
  query_scope = "COLLECTION"
  fields {
    field_path = "ownerId"
    order      = "ASCENDING"
  }
  fields {
    field_path = "state"
    order      = "ASCENDING"
  }
  fields {
    field_path = "createdAt"
    order      = "DESCENDING"
  }
  fields {
    field_path = "__name__"
    order      = "ASCENDING"
  }
}

# Real metadata tests use the same schema in isolated collections so live media
# and GPU dispatchers cannot consume synthetic fixtures. No extra database or
# paid feature; only Development needs this matching pagination index.
resource "google_firestore_index" "integration_owned_projects" {
  count       = var.environment == "dev" ? 1 : 0
  project     = var.project_id
  database    = google_firestore_database.app.name
  collection  = "integration-projects"
  query_scope = "COLLECTION"
  fields {
    field_path = "ownerId"
    order      = "ASCENDING"
  }
  fields {
    field_path = "state"
    order      = "ASCENDING"
  }
  fields {
    field_path = "createdAt"
    order      = "DESCENDING"
  }
  fields {
    field_path = "__name__"
    order      = "ASCENDING"
  }
}
