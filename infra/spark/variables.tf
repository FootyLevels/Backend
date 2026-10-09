variable "project_id" {
  type        = string
  description = "Existing project without a linked billing account."
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "Supply a valid project ID."
  }
}

variable "environment" {
  type        = string
  description = "Separate dev or demo project and local state workspace."
  validation {
    condition     = contains(["dev", "demo"], var.environment)
    error_message = "Choose dev or demo."
  }
}

variable "region" {
  type        = string
  default     = "northamerica-northeast1"
  description = "Proposed Canadian Firestore location; confirm before the first database creation."
  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]+$", var.region))
    error_message = "Choose a regional Firestore location."
  }
}
