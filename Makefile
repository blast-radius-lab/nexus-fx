.PHONY: up dev down down-clean gateway-logs engine-logs price-logs psql

help: ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

up: ## start production-like stack
	docker-compose up -d

dev: ## start with dev overrides (hot-reload)
	docker-compose -f docker-compose.yml -f docker-compose.dev.yml up

down: ## stop everything
	docker-compose down

down-clean: ## stop and remove volumes
	docker-compose down -v

gateway-logs: ## tail api-gateway logs
	docker-compose logs -f api-gateway

engine-logs: ## tail engine logs
	docker-compose logs -f engine

price-logs: ## tail price-service logs
	docker-compose logs -f price-service

psql: ## shell into the database
	docker exec -it lab-postgres-1 psql -U nexus -d nexus
