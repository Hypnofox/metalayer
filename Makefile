.PHONY: venv test run docker-build docker-run

venv:
	python -m venv .venv
	.venv/bin/pip install -r requirements-dev.txt -r requirements.txt

test:
	pytest

run:
	python main.py --host 0.0.0.0 --port 8000

docker-build:
	docker build -t metalayer:latest .

docker-run:
	docker run -p 8000:8000 metalayer:latest
