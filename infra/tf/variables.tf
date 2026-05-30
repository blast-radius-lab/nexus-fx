variable "project_name" {
  type    = string
  default = "nexus"
}

variable "environment" {
  type    = string
  default = "lab"
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

# --- Compute (engine) ---

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
  default = 1
}

variable "container_image_tag" {
  type    = string
  default = "latest"
}

# --- Compute (price) ---

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
  default = 1
}

# --- Compute (gateway) ---

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
  default = 1
}