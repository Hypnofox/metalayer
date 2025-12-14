# Test directory for Kubernetes Semantic Identity Resolver

This directory contains unit tests for the project.

## Running Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_api.py

# Run with coverage
pytest --cov=. --cov-report=html
```

## Test Structure

- `test_api.py` - Tests for REST API endpoints
- `test_watcher.py` - Tests for pod watching functionality

## Test Coverage

The tests cover:
- API endpoint responses and validation
- Flow correlation logic
- Pod lease table operations
- Watcher lifecycle management
- Error handling
