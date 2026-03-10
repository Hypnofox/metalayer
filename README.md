# Kubernetes Semantic Identity Resolver

A Python microservice that connects to the Kubernetes API to build and maintain a Pod Lease Table, mapping pod IP addresses to their Kubernetes identities for network flow correlation.

## Overview

This service provides semantic identity resolution for Kubernetes workloads by:

1. **Connecting to the Kubernetes API** - Establishes connection using kubeconfig or in-cluster configuration
2. **Building a Pod Lease Table** - Creates and maintains a mapping of pod IPs to their metadata (name, namespace, labels, service account, etc.)
3. **Real-time Pod Watching** - Continuously monitors pod changes (ADDED/MODIFIED/DELETED events)
4. **REST API** - Exposes HTTP endpoints for flow correlation and pod identity lookups
5. **Correlating Network Flows** - Matches network flow data (source/destination IPs) with pod identities
6. **Database Integration** - Queries network flows from PostgreSQL database
7. **Topology Export** - Generates Faddom-compatible topology JSON with nodes and edges

## Features

- **Automatic Kubernetes Discovery**: Supports both in-cluster and external kubeconfig-based authentication
- **Real-time Updates**: Uses Kubernetes watch API to track pod changes in real-time
- **Pod Identity Mapping**: Tracks pod metadata including:
  - Pod name and namespace
  - Labels and annotations
  - Service account
  - Node assignment
  - UID and phase
- **REST API**: FastAPI-based HTTP endpoints with OpenAPI documentation
- **Flow Correlation**: Map network flows to Kubernetes workload identities
- **Database Integration**: PostgreSQL connector for querying network flows
- **Topology Export**: Generate Faddom-compatible topology JSON
- **Deployment Grouping**: Aggregate flows by deployment or pod level
- **Metrics**: Prometheus-compatible metrics endpoint
- **Thread-safe**: Safe concurrent access to pod lease table

## Project Structure

```
metalayer/
├── main.py              # Main entry point and core logic
├── api.py               # FastAPI REST API implementation
├── db.py                # PostgreSQL database connector
├── correlator.py        # Flow correlation engine
├── exporter.py          # Topology export to Faddom format
├── requirements.txt     # Python dependencies
├── .env.example         # Example environment configuration
├── config/             # Configuration directory
│   └── kubeconfig      # (Optional) Custom Kubernetes config
├── tests/              # Unit tests
│   ├── test_api.py     # API endpoint tests
│   ├── test_correlator.py  # Flow correlation tests
│   ├── test_exporter.py    # Topology export tests
│   └── test_watcher.py # Pod watcher tests
└── README.md           # This file
```

## Installation

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Database** (for flow correlation):
   ```bash
   # Copy example environment file
   cp .env.example .env
   
   # Edit .env with your PostgreSQL credentials
   # DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
   ```

3. **Configure Kubernetes access** (choose one):
   
   - **Option A - In-cluster**: Deploy in a Kubernetes cluster with appropriate RBAC permissions
   - **Option B - Kubeconfig**: Place your kubeconfig file in `config/kubeconfig`
   - **Option C - Default**: Use your default kubeconfig from `~/.kube/config`

## Usage

### Running with API Server (Default)

```bash
# Start with API server on default port 8000
python main.py

# Specify custom host and port
python main.py --host 0.0.0.0 --port 9000

# Use custom kubeconfig
python main.py --kubeconfig /path/to/kubeconfig
```

The API server will be available at `http://localhost:8000` with interactive documentation at `http://localhost:8000/docs`.

### Running Standalone (Watcher Only)

```bash
# Run without API server
python main.py --mode standalone
```

### API Endpoints

#### Flow Correlation
```bash
# Correlate a network flow
curl -X POST http://localhost:8000/api/v1/correlate \
  -H "Content-Type: application/json" \
  -d '{
    "source_ip": "10.244.1.5",
    "destination_ip": "10.244.2.10"
  }'
```

**Response:**
```json
{
  "source_ip": "10.244.1.5",
  "destination_ip": "10.244.2.10",
  "source_identity": {
    "name": "frontend-pod",
    "namespace": "default",
    "labels": {"app": "frontend"},
    "uid": "abc-123",
    "node_name": "node-1",
    "service_account": "default",
    "phase": "Running"
  },
  "destination_identity": {
    "name": "backend-pod",
    "namespace": "default",
    "labels": {"app": "backend"},
    "uid": "def-456",
    "node_name": "node-2",
    "service_account": "backend-sa",
    "phase": "Running"
  },
  "timestamp": 1702123456.789
}
```

#### List All Pods
```bash
curl http://localhost:8000/api/v1/pods
```

#### Get Pod by IP
```bash
curl http://localhost:8000/api/v1/pods/10.244.1.5
```

#### Health Check
```bash
curl http://localhost:8000/health
```

**Response:**
```json
{
  "status": "healthy",
  "kubernetes_connected": true,
  "pods_tracked": 42,
  "uptime_seconds": 3600.5
}
```

#### Metrics (Prometheus)
```bash
curl http://localhost:8000/metrics
```

#### Export Topology
```bash
# Export topology from network flows
curl -X POST http://localhost:8000/api/v1/export-topology \
  -H "Content-Type: application/json" \
  -d '{
    "start_time": "2025-12-14T00:00:00Z",
    "end_time": "2025-12-14T23:59:59Z",
    "limit": 10000,
    "group_by_deployment": true
  }'
```

**Response:**
```json
{
  "metadata": {
    "generated_at": "2025-12-14T15:30:00Z",
    "start_time": "2025-12-14T00:00:00Z",
    "end_time": "2025-12-14T23:59:59Z",
    "total_flows": 1523,
    "total_nodes": 12,
    "total_edges": 34
  },
  "nodes": [
    {
      "id": "deployment:default:nginx-deployment",
      "type": "deployment",
      "name": "nginx-deployment",
      "namespace": "default",
      "labels": {"app": "nginx"},
      "metadata": {"app": "nginx"}
    }
  ],
  "edges": [
    {
      "source": "deployment:default:nginx-deployment",
      "destination": "deployment:default:redis-deployment",
      "protocol": "TCP",
      "bytes": 1048576,
      "connections": 42
    }
  ]
}
```

### API Documentation

Interactive API documentation is available at:
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`

## Configuration

### Kubernetes RBAC

If running in-cluster, ensure the service account has permissions to list and watch pods:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: pod-reader
rules:
- apiGroups: [""]
  resources: ["pods"]
  verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: pod-reader-binding
subjects:
- kind: ServiceAccount
  name: identity-resolver
  namespace: default
roleRef:
  kind: ClusterRole
  name: pod-reader
  apiGroup: rbac.authorization.k8s.io
```

### Custom Kubeconfig

Place your kubeconfig file at `config/kubeconfig` to use a specific cluster configuration.

### Database Configuration

The service requires PostgreSQL database connection for flow correlation. Configure via environment variables:

```bash
# Required
export DB_HOST=localhost
export DB_PORT=5432
export DB_NAME=faddom
export DB_USER=faddom
export DB_PASSWORD=your_password

# Optional
export DB_MIN_CONN=1
export DB_MAX_CONN=10
export DB_FLOW_TABLE=network_flows
```

Or use a `.env` file (copy from `.env.example`).

### Database Schema

The service expects a flows table with the following schema:

```sql
CREATE TABLE network_flows (
    id SERIAL PRIMARY KEY,
    source_ip VARCHAR(45) NOT NULL,
    dest_ip VARCHAR(45) NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    protocol VARCHAR(10),
    bytes BIGINT
);

CREATE INDEX idx_flows_timestamp ON network_flows(timestamp);
CREATE INDEX idx_flows_source_ip ON network_flows(source_ip);
CREATE INDEX idx_flows_dest_ip ON network_flows(dest_ip);
```

## Architecture

### Core Components

- **K8sIdentityResolver**: Main orchestrator that manages Kubernetes API connection and coordinates pod tracking
- **PodLeaseTable**: In-memory data structure maintaining the IP-to-identity mapping
- **Pod Watcher**: Background thread that monitors Kubernetes pod events in real-time
- **DatabaseConnector**: PostgreSQL connection pool for querying network flows
- **FlowCorrelator**: Correlates network flows with pod identities using the lease table
- **TopologyExporter**: Generates Faddom-compatible topology JSON from correlated flows
- **FastAPI Server**: REST API layer for external integrations

### Data Flow

```
Kubernetes API → Pod Watcher → PodLeaseTable ↓
                                              ↓
PostgreSQL DB → Flow Query → Flow Correlator → Topology Exporter → API Response
```

### Real-time Updates

The service uses Kubernetes watch API to receive real-time notifications:
- **ADDED**: New pod created → Add to lease table
- **MODIFIED**: Pod updated → Update lease table entry
- **DELETED**: Pod removed → Remove from lease table

## Development

### Running Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run with coverage
pytest --cov=. --cov-report=html
```

### Code Structure

The main components are organized as:
- **main.py**: Core resolver logic and pod watching
- **api.py**: FastAPI application and endpoints
- **tests/**: Unit tests for all components

## Deployment

### Docker

Build and run the container using the Makefile:
```bash
make docker-build
make docker-run
```

### OpenShift

We provide a complete set of OpenShift manifests in the `openshift/` directory. These manifests define a Deployment, Service, Route, and ConfigMap.

To deploy to OpenShift:

1. Ensure you are logged into your OpenShift cluster (`oc login ...`).
2. Make sure the `metalayer:latest` image is available in your cluster or update the `image:` property in `openshift/deployment.yaml` to point to your registry.
3. Apply the manifests:

```bash
oc apply -f openshift/
```

This will deploy the application, configure probes to use the `/health` and `/ready` endpoints, and make the application accessible via a Route.

## Requirements

- Python 3.8+
- Kubernetes cluster access
- Appropriate RBAC permissions for pod listing and watching

## Metrics

The service exposes Prometheus metrics at `/metrics`:
- `api_requests_total`: Total API requests by method, endpoint, and status
- `api_request_duration_seconds`: API request duration histogram
- `flow_correlations_total`: Total flow correlation requests

## Future Enhancements

- [ ] Persistent storage for historical data
- [ ] Service-level identity resolution
- [ ] Network policy correlation
- [ ] Support for multiple clusters
- [ ] GraphQL API
- [ ] WebSocket support for real-time updates

## License

[Add your license here]

## Contributing

[Add contribution guidelines here]

