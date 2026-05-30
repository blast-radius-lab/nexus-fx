output "ecs_cluster_name" {
  value = module.compute.cluster_name
}

output "engine_service_name" {
  value = module.compute.engine_service_name
}

output "price_service_name" {
  value = module.compute.price_service_name
}

output "gateway_service_name" {
  value = module.compute.gateway_service_name
}

output "engine_log_group" {
  value = module.compute.engine_log_group
}

output "price_log_group" {
  value = module.compute.price_log_group
}

output "gateway_log_group" {
  value = module.compute.gateway_log_group
}
