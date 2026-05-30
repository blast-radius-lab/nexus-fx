# CI/CD Flow

## Pipeline Overview

```
Feature branch                    main branch                       AWS
─────────────                    ───────────                       ───

Developer pushes
      │
      ▼
Opens PR to main
      │
      ├──────────────► CI runs
      │                  ├─ quality-and-tests (lint + pytest, 3 services in parallel)
      │                  └─ build-and-scan (Trivy + Docker build, 3 services in parallel)
      │                          │
      │                     PR checks pass
      │                          │
      ▼                          ▼
Merges to main ─────────► CI runs again on main
                                 │
                            CI succeeds
                                 │
                                 ▼
                          CD fires (workflow_run)
                                 │
                          ┌──────┴──────┐
                          │  OIDC Auth  │
                          │  ECR Login  │
                          └──────┬──────┘
                                 │
                    ┌────────────┼────────────┐
                    ▼            ▼            ▼
              Build engine  Build price  Build gateway
              Push to ECR   Push to ECR  Push to ECR ──────► ECR repos
                    │            │            │
                    └────────────┼────────────┘
                                 │
                    ┌────────────┼────────────┐
                    ▼            ▼            ▼
              Update engine Update price Update gateway
              task def      task def     task def ─────────► ECS services
                    │            │            │                pull new images
                    └────────────┼────────────┘                and restart
                                 │
                          Wait for stable
                                 │
                                 ▼
                          Deployment complete
```

## Infrastructure Pipeline (Separate)

Infrastructure changes are managed independently through manual workflows:

```
Developer clicks "Run workflow" in GitHub Actions tab
      │
      ├─── Terraform Base (Lab) ──► infra/tf/base/
      │         │                    (ECR repos, IAM OIDC role)
      │         ├─ plan: preview changes
      │         ├─ apply: create/update resources
      │         └─ destroy: tear down resources
      │
      └─── Terraform (Lab) ───────► infra/tf/
                │                    (VPC, ECS, Cloud Map, etc.)
                ├─ plan: preview changes
                ├─ apply: create/update resources
                └─ destroy: tear down resources
```

**Order matters:**
1. Apply base first (ECR repos + deploy role must exist before CD can push images)
2. Apply lab second (ECS cluster needs to exist before CD can deploy to it)
3. Destroy lab first, base second (or leave base permanently)

## What Triggers What

| Event | CI | CD | Terraform |
|-------|----|----|-----------|
| PR opened/updated | Runs (if `services/**` changed) | No | No |
| PR merged to main | Runs (push trigger) | Runs after CI succeeds | No |
| Direct push to main | Runs (if `services/**` changed) | Runs after CI succeeds | No |
| Manual dispatch | No | No | Runs (plan/apply/destroy) |

## Image Tagging Strategy

Each deploy tags images with two values:
- **Git SHA** (`abc123def`): Immutable, identifies exactly which code is running
- **`latest`**: Mutable, convenience tag pointing to the most recent build

ECS task definitions are updated to reference the SHA tag, ensuring deployments are pinned to a specific build.

## Task Definition Update Flow

ECS doesn't let you edit a task definition in place. The CD pipeline:

1. **Fetches** the current task definition JSON from ECS
2. **Replaces** the container image URI with the new SHA-tagged image
3. **Strips** read-only metadata fields that AWS injects but rejects on input
4. **Registers** it as a new revision (e.g., `nexus-lab-engine:3` becomes `nexus-lab-engine:4`)
5. **Updates** the ECS service to point at the new revision
6. ECS performs a **rolling deployment** (minimum 100% healthy, maximum 200%) — new tasks start before old tasks stop

## Failure Scenarios

| Failure | Impact |
|---------|--------|
| CI fails on PR | PR checks fail, merge blocked (if branch protection enabled) |
| CI fails on main | CD workflow fires but skips deploy (`conclusion != 'success'`) |
| Docker build fails in CD | Pipeline stops, ECS keeps running previous version |
| ECS task fails to start | `wait services-stable` times out (10 min default), pipeline fails |
| Terraform apply fails | Partial state possible; re-run apply to converge or fix and re-apply |
