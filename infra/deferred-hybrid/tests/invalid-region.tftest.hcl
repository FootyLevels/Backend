mock_provider "google" {}
mock_provider "google-beta" {}

run "reject_unsupported_queue_region" {
  command = plan
  variables {
    project_id                    = "footylevels-test-project"
    environment                   = "dev"
    acknowledge_billable_services = true
    region                        = "northamerica-northeast2"
  }
  expect_failures = [var.region]
}
