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
