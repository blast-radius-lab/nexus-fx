output "cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "engine_service_name" {
  value = aws_ecs_service.engine.name
}

output "price_service_name" {
  value = aws_ecs_service.price.name
}

output "gateway_service_name" {
  value = aws_ecs_service.gateway.name
}

output "engine_log_group" {
  value = aws_cloudwatch_log_group.engine.name
}

output "price_log_group" {
  value = aws_cloudwatch_log_group.price.name
}

output "gateway_log_group" {
  value = aws_cloudwatch_log_group.gateway.name
}