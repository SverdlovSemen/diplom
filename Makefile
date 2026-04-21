.PHONY: test-backend

test-backend:
	docker compose -f docker-compose.yml -f docker-compose.tests.yml run --rm backend-tests
