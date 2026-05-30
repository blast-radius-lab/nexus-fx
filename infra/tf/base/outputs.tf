output "github_deploy_role_arn" {
  value = module.iam.github_deploy_role_arn
}

output "ecr_repository_url_engine" {
  value = aws_ecr_repository.engine.repository_url
}

output "ecr_repository_url_price" {
  value = aws_ecr_repository.price.repository_url
}

output "ecr_repository_url_gateway" {
  value = aws_ecr_repository.gateway.repository_url
}