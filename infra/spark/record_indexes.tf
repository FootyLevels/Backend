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

# Deliberately private circle feeds. These indexes do not deploy the API or
# change deny-all client security rules. Isolated indexes are Development only.
locals {
  circle_feed_indexes = merge({
    posts    = { collection = "circlePosts", field = "circleId" }
    comments = { collection = "circleComments", field = "postId" }
  }, var.environment == "dev" ? {
    integration_posts    = { collection = "integration-circlePosts", field = "circleId" }
    integration_comments = { collection = "integration-circleComments", field = "postId" }
  } : {})
}
resource "google_firestore_index" "circle_feeds" {
  for_each    = local.circle_feed_indexes
  project     = var.project_id
  database    = google_firestore_database.app.name
  collection  = each.value.collection
  query_scope = "COLLECTION"
  fields {
    field_path = each.value.field
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
