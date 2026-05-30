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

variable "github_org" {
  type    = string
}

variable "github_repo" {
  type    = string
}