"""
Tests for the Kubernetes Identity Resolver API
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, MagicMock
from api import create_app
from main import K8sIdentityResolver, PodLeaseTable


@pytest.fixture
def mock_resolver():
    """Create a mock resolver for testing"""
    resolver = Mock(spec=K8sIdentityResolver)
    resolver.lease_table = PodLeaseTable()
    resolver.v1_api = Mock()
    
    # Add some test data
    resolver.lease_table.update_pod("10.244.1.5", {
        'name': 'test-pod-1',
        'namespace': 'default',
        'labels': {'app': 'test'},
        'uid': 'abc-123',
        'node_name': 'node-1',
        'service_account': 'default',
        'phase': 'Running'
    })
    
    resolver.lease_table.update_pod("10.244.2.10", {
        'name': 'test-pod-2',
        'namespace': 'kube-system',
        'labels': {'app': 'system'},
        'uid': 'def-456',
        'node_name': 'node-2',
        'service_account': 'system',
        'phase': 'Running'
    })
    
    # Mock the correlate_flow method
    def mock_correlate(source_ip, dest_ip):
        return {
            'source_ip': source_ip,
            'destination_ip': dest_ip,
            'source_identity': resolver.lease_table.get_pod_identity(source_ip),
            'destination_identity': resolver.lease_table.get_pod_identity(dest_ip)
        }
    
    resolver.correlate_flow = mock_correlate
    
    return resolver


@pytest.fixture
def client(mock_resolver):
    """Create a test client"""
    app = create_app(mock_resolver)
    return TestClient(app)


class TestHealthEndpoint:
    """Tests for health check endpoint"""
    
    def test_health_check(self, client):
        """Test health check returns correct status"""
        response = client.get("/health")
        assert response.status_code == 200
        
        data = response.json()
        assert data['status'] == 'healthy'
        assert data['kubernetes_connected'] is True
        assert data['pods_tracked'] == 2
        assert 'uptime_seconds' in data


class TestFlowCorrelation:
    """Tests for flow correlation endpoint"""
    
    def test_correlate_both_pods_found(self, client):
        """Test correlation when both pods are found"""
        response = client.post("/api/v1/correlate", json={
            "source_ip": "10.244.1.5",
            "destination_ip": "10.244.2.10"
        })
        
        assert response.status_code == 200
        data = response.json()
        
        assert data['source_ip'] == "10.244.1.5"
        assert data['destination_ip'] == "10.244.2.10"
        assert data['source_identity'] is not None
        assert data['source_identity']['name'] == 'test-pod-1'
        assert data['destination_identity'] is not None
        assert data['destination_identity']['name'] == 'test-pod-2'
    
    def test_correlate_source_not_found(self, client):
        """Test correlation when source pod is not found"""
        response = client.post("/api/v1/correlate", json={
            "source_ip": "192.168.1.1",
            "destination_ip": "10.244.2.10"
        })
        
        assert response.status_code == 200
        data = response.json()
        
        assert data['source_identity'] is None
        assert data['destination_identity'] is not None
    
    def test_correlate_neither_found(self, client):
        """Test correlation when neither pod is found"""
        response = client.post("/api/v1/correlate", json={
            "source_ip": "192.168.1.1",
            "destination_ip": "192.168.1.2"
        })
        
        assert response.status_code == 200
        data = response.json()
        
        assert data['source_identity'] is None
        assert data['destination_identity'] is None
    
    def test_correlate_invalid_request(self, client):
        """Test correlation with invalid request"""
        response = client.post("/api/v1/correlate", json={
            "source_ip": "10.244.1.5"
            # Missing destination_ip
        })
        
        assert response.status_code == 422  # Validation error


class TestPodEndpoints:
    """Tests for pod listing and lookup endpoints"""
    
    def test_list_all_pods(self, client):
        """Test listing all pods"""
        response = client.get("/api/v1/pods")
        
        assert response.status_code == 200
        data = response.json()
        
        assert data['total_pods'] == 2
        assert '10.244.1.5' in data['pods']
        assert '10.244.2.10' in data['pods']
    
    def test_get_pod_by_ip_found(self, client):
        """Test getting pod by IP when it exists"""
        response = client.get("/api/v1/pods/10.244.1.5")
        
        assert response.status_code == 200
        data = response.json()
        
        assert data['name'] == 'test-pod-1'
        assert data['namespace'] == 'default'
        assert data['labels']['app'] == 'test'
    
    def test_get_pod_by_ip_not_found(self, client):
        """Test getting pod by IP when it doesn't exist"""
        response = client.get("/api/v1/pods/192.168.1.1")
        
        assert response.status_code == 404
        assert 'No pod found' in response.json()['detail']


class TestMetrics:
    """Tests for metrics endpoint"""
    
    def test_metrics_endpoint(self, client):
        """Test metrics endpoint returns Prometheus format"""
        response = client.get("/metrics")
        
        assert response.status_code == 200
        assert 'text/plain' in response.headers['content-type']
        
        # Check for expected metrics
        content = response.text
        assert 'api_requests_total' in content or 'python_info' in content
