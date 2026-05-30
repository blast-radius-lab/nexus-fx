data "aws_caller_identity" "current" {}

locals {
  ecr_base = "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.aws_region}.amazonaws.com/${var.project_name}-${var.environment}"
}

# --- Networking ---

module "networking" {
  source = "./modules/networking"

  project_name        = var.project_name
  environment         = var.environment
  vpc_cidr            = "10.1.0.0/16"
}

# --- Compute (engine+price+gateway) ---

module "compute" {
  source = "./modules/compute"

  project_name          = var.project_name
  environment           = var.environment
  aws_region            = var.aws_region
  ecs_subnet_ids        = module.networking.public_subnet_ids
  ecs_security_group_id = module.networking.ecs_security_group_id

  engine_cpu           = var.engine_cpu
  engine_memory        = var.engine_memory
  engine_desired_count = var.engine_desired_count
  engine_max_count     = var.engine_max_count

  price_cpu           = var.price_cpu
  price_memory        = var.price_memory
  price_desired_count = var.price_desired_count
  price_max_count     = var.price_max_count

  gateway_cpu             = var.gateway_cpu
  gateway_memory          = var.gateway_memory
  gateway_desired_count   = var.gateway_desired_count
  gateway_max_count       = var.gateway_max_count

  engine_image  = "${local.ecr_base}-engine:${var.container_image_tag}"
  price_image   = "${local.ecr_base}-price:${var.container_image_tag}"
  gateway_image = "${local.ecr_base}-gateway:${var.container_image_tag}"

  namespace_id = module.networking.namespace_id
}

