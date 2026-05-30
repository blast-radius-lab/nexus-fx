# GitHub Actions Workflows

## Overview

| Workflow | File | Trigger | Purpose |
|----------|------|---------|---------|
| Nexus CI | `ci.yaml` | PR to main, push to main | Lint, test, build, security scan |
| Deploy to AWS | `cd.yml` | After CI succeeds on main | Build/push images, deploy to ECS |
| Terraform (Lab) | `terraform.yml` | Manual (`workflow_dispatch`) | Plan/apply/destroy lab infra |
| Terraform Base | `terraform-base.yml` | Manual (`workflow_dispatch`) | Plan/apply/destroy base infra |

## Nexus CI (`ci.yaml`)

**Triggers:** Push to `main` OR pull request to `main`, when `services/**` or `.github/workflows/ci.yaml` changes.

### Job 1: `quality-and-tests`

Runs in parallel for each service via matrix strategy (`api-gateway`, `engine`, `price-service`). `fail-fast: false` ensures all services are tested even if one fails.

1. **Checkout** code
2. **Ruff** lint and format check (no install needed, uses `astral-sh/ruff-action`)
3. **Setup Python** 3.11 with pip cache keyed to each service's `requirements.txt`
4. **Install dependencies** (pip + pytest + service requirements)
5. **Run pytest** from within each service directory

### Job 2: `build-and-scan`

Runs after `quality-and-tests` passes. Same matrix strategy.

1. **Trivy filesystem scan** on the service source directory (CRITICAL + HIGH only, ignore unfixed)
2. **Docker Buildx** setup for optimized builds
3. **Docker build** locally (`push: false`, `load: true`) with GHA build cache
4. **Trivy image scan** on the built container (uses `.trivyignore` for known exceptions)

## Deploy to AWS (`cd.yml`)

**Trigger:** `workflow_run` — fires after "Nexus CI" completes on the `main` branch. The deploy job has a condition: `if: github.event.workflow_run.conclusion == 'success'`. If CI fails, the job is skipped.

### Environment Variables

| Variable | Value | Purpose |
|----------|-------|---------|
| `AWS_REGION` | `us-east-1` | Target AWS region |
| `PROJECT` | `nexus-lab` | Naming prefix for all AWS resources |

### Steps

1. **Checkout** code
2. **OIDC authentication** to AWS via `aws-actions/configure-aws-credentials`. Uses `AWS_DEPLOY_ROLE_ARN` repo secret. Requires `id-token: write` permission so the runner can request a JWT to prove its identity to AWS.
3. **ECR login** via `aws-actions/amazon-ecr-login`. Outputs `registry` (e.g., `123456789012.dkr.ecr.us-east-1.amazonaws.com`).
4. **Build and push** each service image (`engine`, `price`, `gateway`), tagged with both git SHA and `latest`:
   ```
   <registry>/nexus-lab-engine:<sha>
   <registry>/nexus-lab-price:<sha>
   <registry>/nexus-lab-gateway:<sha>
   ```
5. **Deploy to ECS** for each service (engine, price, gateway):
   - Fetch current task definition from ECS
   - Swap in the new image URI via `jq`
   - Strip read-only fields (`taskDefinitionArn`, `revision`, `status`, `requiresAttributes`, `compatibilities`, `registeredAt`, `registeredBy`) that AWS rejects on re-registration
   - Register as a new task definition revision
   - Update the ECS service to use the new revision with `--force-new-deployment`
6. **Wait for stabilization** — `aws ecs wait services-stable` for each service sequentially

### Required Secrets

| Secret | Description |
|--------|-------------|
| `AWS_DEPLOY_ROLE_ARN` | ARN of the GitHub OIDC deploy role from the base Terraform stack |

## Terraform Workflows

Both `terraform.yml` and `terraform-base.yml` follow the same pattern, differing only in working directory (`infra/tf/` vs `infra/tf/base/`).

**Trigger:** `workflow_dispatch` with a required choice input: `plan`, `apply`, or `destroy`. You run these manually from the GitHub Actions tab.

### Steps

1. **Checkout** code
2. **OIDC authentication** to AWS (same role as CD)
3. **Setup Terraform** (~> 1.9)
4. **`terraform init`**
5. **Plan step** (always runs):
   - For `plan` or `apply`: `terraform plan -out=tfplan`
   - For `destroy`: `terraform plan -destroy -out=tfdestroyplan`
6. **Execute step** (conditional):
   - For `apply`: `terraform apply -auto-approve tfplan`
   - For `destroy`: `terraform apply -auto-approve tfdestroyplan`

The saved plan file (`-out=`) guarantees you apply exactly what was planned. The destroy plan is also applied with `terraform apply` (not `terraform destroy`) because the destroy intent is encoded in the plan file itself.

### Permissions

Both workflows require:
- `id-token: write` — for OIDC authentication
- `contents: read` — to checkout the repository
