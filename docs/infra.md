# Terraform Infrastructure

## Two-Stack Pattern

The infrastructure uses two independent Terraform root modules, each with its own state file. They are applied and destroyed independently.

### Base Stack (`infra/tf/base/`)

Long-lived resources that survive teardowns:

- **3 ECR repositories** (`nexus-lab-engine`, `nexus-lab-price`, `nexus-lab-gateway`) with lifecycle policies keeping the last 10 images
- **IAM OIDC role** that lets GitHub Actions authenticate to AWS without stored credentials

Apply this once and leave it. When you destroy the lab environment to save money, ECR repos and the deploy role stay intact.

### Lab Stack (`infra/tf/`)

Ephemeral resources you spin up and tear down freely:

- **VPC** with 2 public subnets across 2 AZs, internet gateway, route tables
- **ECS Fargate cluster** with 4 services (engine, price, gateway, postgres)
- **Cloud Map** private DNS namespace (`sre-lab.internal`) for service discovery
- **IAM execution role** (pulls images, writes logs) and **task role** (SSM exec access)
- **Auto-scaling** policies for engine, price, and gateway (target 60% CPU)
- **CloudWatch log groups** with 7-day retention

The two stacks have no Terraform dependency between them. The lab stack constructs ECR image URLs from the AWS account ID at plan time (`data.aws_caller_identity`) rather than reading from the base stack's state. This avoids needing an S3 backend or `terraform_remote_state`.

## Module Structure

```
infra/tf/
├── main.tf              # Calls networking + compute modules, constructs ECR URLs
├── variables.tf         # project_name, environment, per-service sizing, image tag
├── outputs.tf           # Cluster name, service names, log groups
├── providers.tf         # AWS provider config with default tags
│
├── base/                # Independent root module (separate state)
│   ├── main.tf          # Calls IAM module, creates 3 ECR repos
│   ├── variables.tf
│   ├── outputs.tf       # Deploy role ARN, ECR repo URLs
│   └── providers.tf
│
└── modules/             # Reusable modules (no state of their own)
    ├── networking/       # VPC, subnets, IGW, Cloud Map namespace, security group
    ├── compute/          # ECS cluster, task defs, services, auto-scaling, log groups
    └── iam/              # GitHub OIDC provider + deploy role
```

## Service Discovery

Each ECS service registers with a Cloud Map discovery service. When a task starts, ECS automatically adds its IP to the private DNS namespace. When it stops, the IP is removed. Services resolve each other by name:

| Service  | DNS Name                       | Port |
|----------|--------------------------------|------|
| Postgres | `pg.sre-lab.internal`          | 5432 |
| Price    | `price.sre-lab.internal`       | 8001 |
| Engine   | `engine.sre-lab.internal`      | 8002 |
| Gateway  | `gateway.sre-lab.internal`     | 8000 |

This replaces what Docker Compose gives you for free with container names on a shared network.

## Security Group Rules

One security group shared by all services, three ingress rules:

| Port(s)    | Source          | Purpose                                    |
|------------|-----------------|--------------------------------------------|
| 8000       | `0.0.0.0/0`    | Public internet reaches the gateway        |
| 8001-8002  | `self` (same SG)| Inter-service communication                |
| 5432       | `self` (same SG)| Services can reach Postgres                |

All egress is allowed (`0.0.0.0/0`). The `self = true` rule means any member of the security group can talk to any other member on those ports.

## IAM Roles

**Execution role** (`nexus-lab-execution`): Used by ECS to pull container images from ECR and write logs to CloudWatch. This is an infrastructure-level role that ECS itself assumes.

**Task role** (`nexus-lab-task`): Used by running containers at runtime. Currently grants SSM permissions for `ecs exec` (SSH-like access to containers for debugging).

**GitHub deploy role** (in base stack): Federated identity via OIDC. GitHub Actions proves its identity with a short-lived JWT, assumes this role to push images to ECR and update ECS services. No long-lived AWS credentials stored in GitHub.

## Applying Infrastructure

1. Apply the base stack first (creates ECR repos + OIDC role):
   - GitHub Actions: Run `Terraform Base (Lab)` workflow with action `apply`
   - Local: `cd infra/tf/base && terraform init && terraform apply`

2. Apply the lab stack (creates VPC, ECS, everything else):
   - GitHub Actions: Run `Terraform (Lab)` workflow with action `apply`
   - Local: `cd infra/tf && terraform init && terraform apply`

3. To tear down the lab (save costs), destroy only the lab stack:
   - Run `Terraform (Lab)` workflow with action `destroy`
   - Base stack resources (ECR repos, deploy role) persist
