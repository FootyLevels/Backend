variable "project_id" {
  description = "Existing, billing-enabled GCP project. Use a different project for each environment."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "Supply a real GCP project ID, not an example placeholder."
  }
}

variable "environment" {
  type        = string
  description = "Environment selected before initialization of its separate state."
  validation {
    condition     = contains(["dev", "demo"], var.environment)
    error_message = "Environment must be dev or demo."
  }
}

variable "region" {
  type        = string
  description = "Institution-approved regional location for data and cloud services. No assumed residency."
  validation {
    condition = contains([
      "northamerica-northeast1", "southamerica-east1", "us-central1", "us-east1", "us-east4",
      "us-south1", "us-west1", "us-west2", "us-west3", "us-west4",
      "europe-central2", "europe-west1", "europe-west2", "europe-west3", "europe-west6",
      "asia-east1", "asia-east2", "asia-northeast1", "asia-northeast2", "asia-northeast3",
      "asia-south1", "asia-southeast1", "asia-southeast2", "australia-southeast1",
      "me-central1", "me-central2",
    ], var.region)
    error_message = "Choose an institution-approved Cloud Tasks region. Toronto is currently unsupported; Montreal is northamerica-northeast1."
  }
}

variable "api_image" {
  type        = string
  default     = ""
  description = "Reviewed CPU API image pinned by sha256 digest. Empty leaves the service undeployed."
  validation {
    condition     = var.api_image == "" || can(regex("@sha256:[a-f0-9]{64}$", var.api_image))
    error_message = "API image must be empty or pinned by a sha256 digest."
  }
}

variable "media_image" {
  type        = string
  default     = ""
  description = "Reviewed CPU media/maintenance image by digest. Empty disables service and schedule."
  validation {
    condition     = var.media_image == "" || can(regex("@sha256:[a-f0-9]{64}$", var.media_image))
    error_message = "Media image must be empty or pinned by a sha256 digest."
  }
}

variable "enable_public_api" {
  type        = bool
  default     = false
  description = "Enable only after FP-14 verifies Firebase tokens and resource ownership in the chosen image."
}

variable "dgx_federated_principal" {
  type        = string
  default     = null
  nullable    = true
  description = "Approved, narrowly mapped WIF principal/principalSet for DGX. Null grants no external access."
  validation {
    condition = var.dgx_federated_principal == null ? true : can(regex(
      "^principal(Set)?://iam\\.googleapis\\.com/projects/[0-9]+/locations/global/workloadIdentityPools/[^/]+/(subject|attribute\\.[^/]+)/[^*]+$",
      var.dgx_federated_principal
    ))
    error_message = "Use a scoped WIF subject or attribute principal, never a whole-pool wildcard or service-account key."
  }
}

variable "media_retention_days" {
  type        = number
  default     = 30
  description = "Maximum age of source and generated user media. Metadata expiry is handled by the application."
  validation {
    condition     = var.media_retention_days >= 1 && var.media_retention_days <= 90 && floor(var.media_retention_days) == var.media_retention_days
    error_message = "Choose an integer retention of 1–90 days."
  }
}
variable "acknowledge_billable_services" {
  type        = bool
  default     = false
  description = "Blocked by the free-only requirement. Only change after explicit new authorization for billable cloud services."
  validation {
    condition     = var.acknowledge_billable_services
    error_message = "The full hybrid stack requires billing and is deferred. The current user requirement is free services only; use infra/spark."
  }
}
