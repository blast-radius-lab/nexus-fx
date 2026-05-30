locals {
  name_prefix = "${var.project_name}-${var.environment}"
}

# --- ECS Cluster ---

resource "aws_ecs_cluster" "main" {
  name = local.name_prefix

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

# --- CloudWatch Log Groups ---

resource "aws_cloudwatch_log_group" "engine" {
  name              = "/ecs/${local.name_prefix}/engine"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "price" {
  name              = "/ecs/${local.name_prefix}/price"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "gateway" {
  name              = "/ecs/${local.name_prefix}/gateway"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "pg" {
  name              = "/ecs/${local.name_prefix}/pg"
  retention_in_days = 7
}

# --- IAM Roles ---

data "aws_caller_identity" "current" {}

resource "aws_iam_role" "execution" {
  name = "${local.name_prefix}-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "execution" {
  name = "${local.name_prefix}-execution"
  role = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_iam_role" "task" {
  name = "${local.name_prefix}-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "task" {
  name = "${local.name_prefix}-task"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssmmessages:CreateControlChannel",
          "ssmmessages:CreateDataChannel",
          "ssmmessages:OpenControlChannel",
          "ssmmessages:OpenDataChannel",
        ]
        Resource = "*"
      },
    ]
  })
}

# --- Engine Task Definition ---

resource "aws_ecs_task_definition" "engine" {
  family                   = "${local.name_prefix}-engine"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.engine_cpu
  memory                   = var.engine_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = "engine"
    image     = var.engine_image
    essential = true

    portMappings = [{
      containerPort = 8002
      protocol      = "tcp"
    }]

    environment = [
      { name = "POSTGRES_HOST", value = "pg.sre-lab.internal" },
      { name = "POSTGRES_PORT", value = "5432" },
      { name = "POSTGRES_DB", value = "nexus" },
      { name = "POSTGRES_USER", value = "nexus" },
      { name = "POSTGRES_PASSWORD", value = "nexus_dev" },
      { name = "PRICE_SERVICE_URL", value = "http://price.sre-lab.internal:8001" },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.engine.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "engine"
      }
    }

    healthCheck = {
      command     = ["CMD-SHELL", "wget -qO- http://localhost:8002/health || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 10
    }
  }])
}

# --- Engine Service ---

resource "aws_ecs_service" "engine" {
  name            = "${local.name_prefix}-engine"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.engine.arn
  desired_count   = var.engine_desired_count
  launch_type     = "FARGATE"

  enable_execute_command = true

  network_configuration {
    subnets          = var.ecs_subnet_ids
    security_groups  = [var.ecs_security_group_id]
    assign_public_ip = true
  }

  service_registries {
    registry_arn = aws_service_discovery_service.engine.arn
    container_name = "engine"
  }
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  lifecycle {
    ignore_changes = [desired_count]
  }
}

# --- Engine Auto-scaling ---

resource "aws_appautoscaling_target" "engine" {
  max_capacity       = var.engine_max_count
  min_capacity       = var.engine_desired_count
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.engine.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "engine_cpu" {
  name               = "${local.name_prefix}-engine-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.engine.resource_id
  scalable_dimension = aws_appautoscaling_target.engine.scalable_dimension
  service_namespace  = aws_appautoscaling_target.engine.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value = 60.0
  }
}

# --- Price Task Definition ---

resource "aws_ecs_task_definition" "price" {
  family                   = "${local.name_prefix}-price"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.price_cpu
  memory                   = var.price_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = "price"
    image     = var.price_image
    essential = true

    portMappings = [{
      containerPort = 8001
      protocol      = "tcp"
    }]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.price.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "price"
      }
    }

    environment = [
      { name = "OANDA_TOKEN", value = "" },
      { name = "OANDA_ACCOUNT_ID", value = "" },
      { name = "OANDA_ENVIRONMENT", value = "practice" },
    ]

    healthCheck = {
      command     = ["CMD-SHELL", "wget -qO- http://localhost:8001/health || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 10
    }
  }])
}

# --- Price Service ---

resource "aws_ecs_service" "price" {
  name            = "${local.name_prefix}-price"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.price.arn
  desired_count   = var.price_desired_count
  launch_type     = "FARGATE"

  enable_execute_command = true

  network_configuration {
    subnets          = var.ecs_subnet_ids
    security_groups  = [var.ecs_security_group_id]
    assign_public_ip = true
  }

  service_registries {
    registry_arn = aws_service_discovery_service.price.arn
    container_name = "price"
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  lifecycle {
    ignore_changes = [desired_count]
  }
}

# --- Price Auto-scaling ---

resource "aws_appautoscaling_target" "price" {
  max_capacity       = var.price_max_count
  min_capacity       = var.price_desired_count
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.price.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "price_cpu" {
  name               = "${local.name_prefix}-price-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.price.resource_id
  scalable_dimension = aws_appautoscaling_target.price.scalable_dimension
  service_namespace  = aws_appautoscaling_target.price.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value = 60.0
  }
}

# --- Gateway Task Definition ---

resource "aws_ecs_task_definition" "gateway" {
  family                   = "${local.name_prefix}-gateway"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.gateway_cpu
  memory                   = var.gateway_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = "gateway"
    image     = var.gateway_image
    essential = true

    portMappings = [{
      containerPort = 8000
      protocol      = "tcp"
    }]

    environment = [
      { name = "POSTGRES_HOST", value = "pg.sre-lab.internal" },
      { name = "POSTGRES_PORT", value = "5432" },
      { name = "POSTGRES_DB", value = "nexus" },
      { name = "POSTGRES_USER", value = "nexus" },
      { name = "POSTGRES_PASSWORD", value = "nexus_dev" },
      { name = "JWT_SECRET", value = "dev-secret-change-in-prod" },
      { name = "JWT_EXPIRY_MINUTES", value = "60" },
      { name = "PRICE_SERVICE_URL", value = "http://price.sre-lab.internal:8001" },
      { name = "ENGINE_SERVICE_URL", value = "http://engine.sre-lab.internal:8002" },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.gateway.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "gateway"
      }
    }

    healthCheck = {
      command     = ["CMD-SHELL", "wget -qO- http://localhost:8000/health || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 10
    }
  }])
}

# --- Gateway Service ---

resource "aws_ecs_service" "gateway" {
  name            = "${local.name_prefix}-gateway"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.gateway.arn
  desired_count   = var.gateway_desired_count
  launch_type     = "FARGATE"

  enable_execute_command = true

  network_configuration {
    subnets          = var.ecs_subnet_ids
    security_groups  = [var.ecs_security_group_id]
    assign_public_ip = true
  }

  service_registries {
    registry_arn = aws_service_discovery_service.gateway.arn
    container_name = "gateway"
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  lifecycle {
    ignore_changes = [desired_count]
  }
}

# --- Gateway Auto-scaling ---

resource "aws_appautoscaling_target" "gateway" {
  max_capacity       = var.gateway_max_count
  min_capacity       = var.gateway_desired_count
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.gateway.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "gateway_cpu" {
  name               = "${local.name_prefix}-gateway-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.gateway.resource_id
  scalable_dimension = aws_appautoscaling_target.gateway.scalable_dimension
  service_namespace  = aws_appautoscaling_target.gateway.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value = 60.0
  }
}

# --- PG Task Definition ---

resource "aws_ecs_task_definition" "pg" {
  family                   = "${local.name_prefix}-pg"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.pg_cpu
  memory                   = var.pg_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = "pg"
    image     = "postgres:15-alpine"
    essential = true

    portMappings = [{
      containerPort = 5432
      protocol      = "tcp"
    }]

    environment = [
      { name = "POSTGRES_DB", value = "nexus" },
      { name = "POSTGRES_USER", value = "nexus" },
      { name = "POSTGRES_PASSWORD", value = "nexus_dev" },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.pg.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "pg"
      }
    }

    healthCheck = {
      command     = ["CMD-SHELL", "pg_isready -U nexus || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 10
    }
  }])
}

# --- PG Service ---

resource "aws_ecs_service" "pg" {
  name            = "${local.name_prefix}-pg"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.pg.arn
  desired_count   = var.pg_desired_count
  launch_type     = "FARGATE"

  enable_execute_command = true

  network_configuration {
    subnets          = var.ecs_subnet_ids
    security_groups  = [var.ecs_security_group_id]
    assign_public_ip = true
  }

  service_registries {
    registry_arn = aws_service_discovery_service.pg.arn
    container_name = "pg"
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  lifecycle {
    ignore_changes = [desired_count]
  }
}

# --- Services within Private Namespace ---
resource "aws_service_discovery_service" "engine" {
  name = "engine"
  dns_config {
    namespace_id = var.namespace_id

    dns_records {
      ttl = 60
      type = "A"
    }

    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {
    failure_threshold = 1
  }
}

resource "aws_service_discovery_service" "price" {
  name = "price"
  dns_config {
    namespace_id = var.namespace_id

    dns_records {
      ttl = 60
      type = "A"
    }

    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {
    failure_threshold = 1
  }
}

resource "aws_service_discovery_service" "gateway" {
  name = "gateway"
  dns_config {
    namespace_id = var.namespace_id

    dns_records {
      ttl = 60
      type = "A"
    }

    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {
    failure_threshold = 1
  }
}

resource "aws_service_discovery_service" "pg" {
  name = "pg"
  dns_config {
    namespace_id = var.namespace_id

    dns_records {
      ttl = 60
      type = "A"
    }

    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {
    failure_threshold = 1
  }
}