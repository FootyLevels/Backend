output "application_environment" {
  description = "Non-secret configuration contract for future API/media/DGX adapters."
  value       = local.runtime_env
}

output "runtime_service_accounts" {
  value = { for name, account in google_service_account.runtime : name => account.email }
}

output "artifact_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.containers.repository_id}"
}

output "api_url" {
  value = try(google_cloud_run_v2_service.api[0].uri, null)
}

output "media_worker_url" {
  value = try(google_cloud_run_v2_service.media[0].uri, null)
}
