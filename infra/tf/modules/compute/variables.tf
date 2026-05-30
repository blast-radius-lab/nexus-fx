variable "project_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "ecs_subnet_ids" {
  type = list(string)
}

variable "ecs_security_group_id" {
  type = string
}

variable "engine_image" {
  type = string
}

variable "price_image" {
  type = string
}

variable "gateway_image" {
  type = string
}

variable "namespace_id" {
  type = string
}

# Engine service
variable "engine_cpu" {
  type    = number
  default = 512
}

variable "engine_memory" {
  type    = number
  default = 1024
}

variable "engine_desired_count" {
  type    = number
  default = 1
}

variable "engine_max_count" {
  type    = number
  default = 4
}

# Price service
variable "price_cpu" {
  type    = number
  default = 512
}

variable "price_memory" {
  type    = number
  default = 1024
}

variable "price_desired_count" {
  type    = number
  default = 1
}

variable "price_max_count" {
  type    = number
  default = 4
}

# Gateway service
variable "gateway_cpu" {
  type    = number
  default = 512
}

variable "gateway_memory" {
  type    = number
  default = 1024
}

variable "gateway_desired_count" {
  type    = number
  default = 1
}

variable "gateway_max_count" {
  type    = number
  default = 4
}

# PG service
variable "pg_cpu" {
  type    = number
  default = 512
}

variable "pg_memory" {
  type    = number
  default = 512
}

variable "pg_desired_count" {
  type    = number
  default = 1
}
